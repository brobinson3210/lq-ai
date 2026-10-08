"""Urgent Matters endpoint — Management tab, "Urgent Matters" button.

Surface:

* ``GET /api/v1/management/urgent-matters`` — one computed, read-only
  call that answers "what needs my attention right now". No tables of
  its own; it scans four existing sources and buckets findings into
  ``red`` (act today) and ``yellow`` (act this week).

**Sources** (documents scanning is explicitly out of scope — later MCP
work):

1. Open stakeholder commitments, both directions (``we_owe`` /
   ``they_owe``), that carry a due date.
2. Stakeholder cadence breaches — ``days_since_last_interaction``
   greater than ``cadence_target_days`` (stakeholders with no cadence
   target or no logged interactions are never flagged).
3. KPIs whose attainment is at or below 80% of target (below the
   green band of Bill's traffic-light rule), both department- and
   individual-scope.
4. Outside counsel (Bill's round-4 answers, 2026-09-30): the panel's
   rate terms, chosen partners who have left their firm, and spend vs
   budget for the current and previous quarter. Thresholds live in
   :mod:`app.api.management_outside_counsel`.

**Horizon.** The red window for priority stakeholders is 7 days; the
ordinary yellow window is 5 days, because the panel promises "needs
your attention in the next 5 days" and the label should mean what it
says (Bill's notes 2026-07-30). The horizon extends to 14 days for
regulator/auditor stakeholders (visibility only — red for a regulator
still means "less than one week", per Bill's round-3 answers
2026-07-29). Every window rolls off today's date.

**RED** = any of:

* (a) an open commitment past due (``due_date < today``);
* (b) an open commitment involving a regulator, auditor, board chair,
  or CEO due within 7 days;
* (c) an open commitment involving a C-suite peer due within 3 days
  ("urgent for them");
* (i) a chosen outside-counsel partner marked as having left their
  firm (until resolved as replaced or followed);
* (j) a firm whose discount is below the 10% minimum;
* (k) quarter outside-counsel spend at 125%+ of budget.

**YELLOW** = any of:

* (d) any other open commitment due within 5 days;
* (e) a regulator/auditor commitment 8-14 days out (the extended
  visibility window — on the radar, not yet red);
* (f) a stakeholder cadence breach;
* (g) a department-scope KPI at or below 80% attainment (behind, but
  not same-day);
* (h) an individual-scope KPI at or below 80% attainment, titled with
  the team member's name;
* (l) quarter outside-counsel spend at 110-124% of budget;
* (m) a firm whose hourly rate increase is above 5%.

**Ranking.** Red first — commitments most-overdue first (most-negative
``days_until_due``), then soonest due, then outside-counsel reds. Then
yellow — commitments by due date, then cadence breaches by days-over
(largest gap first), then KPIs by attainment ascending, then
outside-counsel yellows.

**Caps.** Each band is capped independently: up to 10 reds and up to 5
yellows. A single combined cap of 10 starved the yellow band entirely
whenever there were ten reds — the band promises "needs your attention
in the next 5 days" and must not go dark just because the reds are
busy (Bill's decision 2026-07-30, superseding the round-3 combined
cap).

**Reserved slots per source.** Within a band, one busy source must not
hide the others (Bill's decision 2026-10-08, after ten overdue
commitments hid every outside-counsel red). Each source present in a
band is guaranteed its top ``min(2, cap // sources_present)`` items
(at least 1) — so 2 each in the red band, 1 each in the 5-slot yellow
band when all four sources are present — and the remaining slots go
to the highest-ranked items overall. The chosen items keep the band's
normal ranking order.

**Per-user isolation.** Owner-scoped exactly as the sibling Management
modules: every query filters on ``owner_id``; soft-deleted rows never
contribute. Read-only — no audit rows. A fixed handful of batched
queries (commitments+stakeholders joined, stakeholders with the cadence
aggregate, KPIs with the latest-value aggregate, firms, departed
partners, quarter spend and budgets) — never N+1.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import ActiveUser
from app.api.management_kpis import _attainment_pct
from app.api.management_outside_counsel import (
    BUDGET_YELLOW_PCT,
    MAX_RATE_INCREASE_PCT,
    MIN_DISCOUNT_PCT,
    budget_band,
    firm_flags,
    money,
    pct_of,
    period_for,
    previous_period,
    quarter_actuals,
    quarter_budgets,
)
from app.db.session import get_db
from app.models.management_kpi import MgmtKpi, MgmtKpiDatapoint, MgmtTeamMember
from app.models.management_outside_counsel import MgmtOcFirm, MgmtOcFirmPartner
from app.models.stakeholder import Stakeholder, StakeholderCommitment, StakeholderInteraction
from app.schemas.management_urgent import UrgentItem, UrgentMattersRead

router = APIRouter(prefix="/management", tags=["management-urgent"])
log = logging.getLogger(__name__)

# The red window for priority stakeholders (regulator/auditor/board
# chair/CEO). Distinct from the yellow window below — narrowing one
# must not narrow the other.
_RED_PRIORITY_HORIZON_DAYS = 7
# The ordinary yellow window, matching the panel's "next 5 days" label.
_YELLOW_HORIZON_DAYS = 5
_REGULATOR_HORIZON_DAYS = 14
_C_SUITE_PEER_HORIZON_DAYS = 3
_KPI_YELLOW_BAND_PCT = Decimal(80)
# Per-band caps — see "Caps" in the module docstring. Independent, so a
# full red band never squeezes the yellow band out of existence.
_MAX_RED_ITEMS = 10
_MAX_YELLOW_ITEMS = 5
# Guaranteed slots per source within a band — see "Reserved slots".
_RESERVED_PER_SOURCE = 2
_TITLE_MAX_LEN = 80

_REGULATORY_TYPES = ("regulator", "auditor")
_TOP_TABLE_TYPES = ("board_chair", "ceo")

_TYPE_LABELS: dict[str, str] = {
    "board_chair": "Board Chair",
    "director": "Director",
    "ceo": "CEO",
    "c_suite_peer": "C-suite Peer",
    "investor_sponsor": "Investor/Sponsor",
    "lender": "Lender",
    "customer": "Customer",
    "regulator": "Regulator",
    "auditor": "Auditor",
    "outside_counsel": "Outside Counsel",
    "media": "Media",
    "other": "Stakeholder",
}

# A ranked entry: (intra-bucket sort key, item). Sort keys are tuples
# whose first element sequences the sub-groups (yellow: 0 = commitments,
# 1 = cadence breaches, 2 = KPIs, 3 = outside counsel; red: 0 =
# commitments by urgency, 1 = outside counsel).
_Ranked = tuple[tuple[int, int | Decimal, str], UrgentItem]


# ---------------------------------------------------------------------------
# Wording helpers
# ---------------------------------------------------------------------------


def _trim_title(text: str, limit: int = _TITLE_MAX_LEN) -> str:
    """Whitespace-collapse and cap the commitment description for a title."""

    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _days_phrase(days_until_due: int) -> str:
    if days_until_due < 0:
        n = -days_until_due
        return f"{n} day{'s' if n != 1 else ''} overdue"
    if days_until_due == 0:
        return "due today"
    return f"{days_until_due} day{'s' if days_until_due != 1 else ''} from now"


def _commitment_item(
    commitment: StakeholderCommitment,
    stakeholder_id: uuid.UUID,
    full_name: str,
    stakeholder_type: str,
    days_until_due: int,
) -> UrgentItem:
    label = _TYPE_LABELS.get(stakeholder_type, "Stakeholder")
    if commitment.direction == "we_owe":
        # We owe it — the GC's to deliver.
        title = f"Deliver: {_trim_title(commitment.description)}"
        owed = f"owed to {full_name} ({label})"
        next_step = "Deliver or renegotiate the date"
    else:
        # They owe it — a chase item.
        title = f"Chase: {_trim_title(commitment.description)}"
        owed = f"owed by {full_name} ({label})"
        next_step = "Chase delivery or renegotiate the date"
    due = commitment.due_date
    assert due is not None  # undated commitments are filtered out in SQL
    return UrgentItem(
        kind="commitment",
        title=title,
        why=f"Due {due.isoformat()} — {_days_phrase(days_until_due)} — {owed}",
        next_step=next_step,
        due_date=due,
        days_until_due=days_until_due,
        stakeholder_id=stakeholder_id,
        stakeholder_name=full_name,
        kpi_id=None,
        link=f"/lq-ai/management/stakeholders/{stakeholder_id}",
    )


def _cap_with_reserved_slots(ranked: list[_Ranked], cap: int) -> list[UrgentItem]:
    """Apply a band's cap without letting one source crowd out the rest.

    ``ranked`` must already be sorted. Each source (``kind``) present is
    guaranteed its top ``min(_RESERVED_PER_SOURCE, cap // sources)``
    items (at least 1); leftover slots go to the best-ranked remainder.
    Output keeps the ranked order.
    """

    if not ranked:
        return []
    kinds = list(dict.fromkeys(item.kind for _, item in ranked))
    per_source = max(1, min(_RESERVED_PER_SOURCE, cap // len(kinds)))
    chosen: set[int] = set()
    for kind in kinds:
        positions = [i for i, (_, item) in enumerate(ranked) if item.kind == kind]
        chosen.update(positions[:per_source])
    for i in range(len(ranked)):
        if len(chosen) >= cap:
            break
        chosen.add(i)
    return [ranked[i][1] for i in sorted(chosen)[:cap]]


def _oc_item(
    *,
    title: str,
    why: str,
    next_step: str,
    firm_id: uuid.UUID | None,
    link: str,
) -> UrgentItem:
    return UrgentItem(
        kind="outside_counsel",
        title=title,
        why=why,
        next_step=next_step,
        due_date=None,
        days_until_due=None,
        stakeholder_id=None,
        stakeholder_name=None,
        kpi_id=None,
        firm_id=firm_id,
        link=link,
    )


def _classify_commitment(stakeholder_type: str, days_until_due: int) -> str | None:
    """Red / yellow / out-of-horizon for one open, dated commitment.

    Encodes rules (a)-(e) from the module docstring. Returns ``"red"``,
    ``"yellow"``, or ``None`` (outside the horizon).
    """

    if days_until_due < 0:
        return "red"  # (a) past due — any stakeholder
    if stakeholder_type in _REGULATORY_TYPES + _TOP_TABLE_TYPES and (
        days_until_due <= _RED_PRIORITY_HORIZON_DAYS
    ):
        return "red"  # (b) regulator/auditor/board chair/CEO within a week
    if stakeholder_type == "c_suite_peer" and days_until_due <= _C_SUITE_PEER_HORIZON_DAYS:
        return "red"  # (c) c-suite peer within 3 days — urgent for them
    if days_until_due <= _YELLOW_HORIZON_DAYS:
        return "yellow"  # (d) everything else due in the next 5 days
    if stakeholder_type in _REGULATORY_TYPES and days_until_due <= _REGULATOR_HORIZON_DAYS:
        return "yellow"  # (e) regulator/auditor 8-14 days out — visible, not red
    return None


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/urgent-matters",
    response_model=UrgentMattersRead,
    summary='The "Urgent Matters" feed — red (act today) and yellow (act this week)',
    description=(
        "Computed and read-only: scans the caller's open commitments, "
        "stakeholder cadence breaches, KPIs at or below 80% "
        "attainment, and outside-counsel signals (discount below 10% "
        "and departed partners red; rate increases above 5% yellow; "
        "quarter spend at 125%+ of budget red, 110%+ yellow), and buckets findings into ``red`` / ``yellow`` per "
        "the triage rule set (overdue commitments and regulator/board-"
        "chair/CEO commitments inside a week are red; other commitments "
        "due in the next 5 days, regulator items 8-14 days out, cadence "
        "breaches, and below-green KPIs are yellow). Each band is capped "
        "independently: up to 10 reds and up to 5 yellows, so a full red "
        "band never starves the yellow one, and each source keeps "
        "reserved slots inside a band (2 each in red; at least 1 each in "
        "yellow) so one busy source cannot hide the others. Documents "
        "scanning is out of scope (later MCP work)."
    ),
)
async def get_urgent_matters(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UrgentMattersRead:
    now = datetime.now(tz=UTC)
    today = now.date()

    red: list[_Ranked] = []
    yellow: list[_Ranked] = []

    # -- Source 1: open commitments (both directions), one joined SELECT.
    # The SQL horizon is the widest band (anything overdue .. +14 days);
    # the per-type narrowing happens in _classify_commitment.
    commitment_stmt = (
        select(
            StakeholderCommitment,
            Stakeholder.id,
            Stakeholder.full_name,
            Stakeholder.stakeholder_type,
        )
        .join(Stakeholder, StakeholderCommitment.stakeholder_id == Stakeholder.id)
        .where(
            Stakeholder.owner_id == user.id,
            Stakeholder.deleted_at.is_(None),
            StakeholderCommitment.status == "open",
            StakeholderCommitment.due_date.is_not(None),
            StakeholderCommitment.due_date <= today + timedelta(days=_REGULATOR_HORIZON_DAYS),
        )
    )
    for commitment, sid, full_name, stakeholder_type in (await db.execute(commitment_stmt)).all():
        days_until_due = (commitment.due_date - today).days
        bucket = _classify_commitment(stakeholder_type, days_until_due)
        if bucket is None:
            continue
        item = _commitment_item(commitment, sid, full_name, stakeholder_type, days_until_due)
        if bucket == "red":
            # Reds sort purely by urgency: most-overdue first, then
            # soonest due (days_until_due ascending), title tiebreak.
            red.append(((0, days_until_due, item.title), item))
        else:
            yellow.append(((0, days_until_due, item.title), item))

    # -- Source 2: cadence breaches — stakeholder rows with the
    # last-interaction aggregate attached (same correlated-subquery
    # pattern as the stakeholders list endpoint; one round-trip).
    last_interaction_sq = (
        select(func.max(StakeholderInteraction.occurred_at))
        .where(StakeholderInteraction.stakeholder_id == Stakeholder.id)
        .correlate(Stakeholder)
        .scalar_subquery()
    )
    cadence_stmt = select(Stakeholder, last_interaction_sq.label("last_interaction_at")).where(
        Stakeholder.owner_id == user.id,
        Stakeholder.deleted_at.is_(None),
        Stakeholder.cadence_target_days.is_not(None),
    )
    for stakeholder, last_at in (await db.execute(cadence_stmt)).all():
        target_days = stakeholder.cadence_target_days
        if last_at is None or target_days is None:
            continue  # no interactions / no cadence — not flagged, per spec
        days_since = max((now - last_at).days, 0)
        if days_since <= target_days:
            continue
        days_over = days_since - target_days
        item = UrgentItem(
            kind="cadence",
            title=f"Reconnect with {stakeholder.full_name}",
            why=f"No touch in {days_since} days vs a {target_days}-day target",
            next_step="Schedule a touchpoint",
            due_date=None,
            days_until_due=None,
            stakeholder_id=stakeholder.id,
            stakeholder_name=stakeholder.full_name,
            kpi_id=None,
            link=f"/lq-ai/management/stakeholders/{stakeholder.id}",
        )
        # Cadence breaches rank after commitments, largest gap first.
        yellow.append(((1, -days_over, item.title), item))

    # -- Source 3: KPIs at or below the yellow band (attainment <= 80%),
    # latest value via the same correlated-subquery pattern as the KPI
    # list endpoint; team-member name outer-joined for individual scope.
    latest_value_sq = (
        select(MgmtKpiDatapoint.value)
        .where(MgmtKpiDatapoint.kpi_id == MgmtKpi.id)
        .correlate(MgmtKpi)
        .order_by(MgmtKpiDatapoint.period.desc())
        .limit(1)
        .scalar_subquery()
    )
    kpi_stmt = (
        select(MgmtKpi, latest_value_sq.label("latest_value"), MgmtTeamMember.name)
        .outerjoin(MgmtTeamMember, MgmtKpi.team_member_id == MgmtTeamMember.id)
        .where(
            MgmtKpi.owner_id == user.id,
            MgmtKpi.deleted_at.is_(None),
        )
    )
    for kpi, latest_value, member_name in (await db.execute(kpi_stmt)).all():
        attainment = _attainment_pct(kpi.direction, kpi.target, latest_value)
        if attainment is None:
            continue  # no target / no datapoints — nothing to judge
        pct = Decimal(attainment)
        if pct > _KPI_YELLOW_BAND_PCT:
            continue
        if kpi.scope == "individual" and member_name is not None:
            title = f"{member_name}: {kpi.name}"  # (h) titled with the member's name
        else:
            title = kpi.name  # (g) department scope
        item = UrgentItem(
            kind="kpi",
            title=title,
            why=f"Attainment {attainment}% of target",
            next_step="Review the trend and the plan with the owner",
            due_date=None,
            days_until_due=None,
            stakeholder_id=None,
            stakeholder_name=None,
            kpi_id=kpi.id,
            link=f"/lq-ai/management/kpis/{kpi.id}",
        )
        # KPIs rank last among yellows, worst attainment first.
        yellow.append(((2, pct, item.title), item))

    # -- Source 4: outside counsel — the panel (rate terms, departed
    # partners) and spend vs budget for the current and previous
    # quarter (invoices often land after quarter close).
    oc_link = "/lq-ai/management/outside-counsel"
    firms = (
        (
            await db.execute(
                select(MgmtOcFirm).where(
                    MgmtOcFirm.owner_id == user.id, MgmtOcFirm.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for firm in firms:
        below, above = firm_flags(firm)
        if below:
            item = _oc_item(
                title=f"Discount below {MIN_DISCOUNT_PCT}%: {firm.name}",
                why=f"{firm.name} gives {firm.discount_pct}% vs the required "
                f"{MIN_DISCOUNT_PCT}% minimum",
                next_step=f"Renegotiate to at least {MIN_DISCOUNT_PCT}% or move the work",
                firm_id=firm.id,
                link=oc_link,
            )
            red.append(((1, 1, item.title), item))  # (j)
        if above:
            year = f" for {firm.rate_year}" if firm.rate_year else ""
            item = _oc_item(
                title=f"Rate increase above {MAX_RATE_INCREASE_PCT}%: {firm.name}",
                why=f"Hourly rates up {firm.rate_increase_pct}%{year} vs the "
                f"{MAX_RATE_INCREASE_PCT}% cap",
                next_step="Push back on the increase before the next invoice cycle",
                firm_id=firm.id,
                link=oc_link,
            )
            yellow.append(((3, 1, item.title), item))  # (m)

    departed = (
        await db.execute(
            select(MgmtOcFirmPartner, MgmtOcFirm.name)
            .join(MgmtOcFirm, MgmtOcFirm.id == MgmtOcFirmPartner.firm_id)
            .where(
                MgmtOcFirm.owner_id == user.id,
                MgmtOcFirm.deleted_at.is_(None),
                MgmtOcFirmPartner.deleted_at.is_(None),
                MgmtOcFirmPartner.status == "left_firm",
            )
        )
    ).all()
    for partner, firm_name in departed:
        since = f" on {partner.left_at.isoformat()}" if partner.left_at else ""
        item = _oc_item(
            title=f"Partner left: {partner.name} ({firm_name})",
            why=f"Marked as having left {firm_name}{since} — you hire partners, not firms",
            next_step=f"Decide: follow {partner.name} to the new firm, or choose a replacement",
            firm_id=partner.firm_id,
            link=oc_link,
        )
        red.append(((1, 0, item.title), item))  # (i)

    this_q = period_for(today)
    periods = [previous_period(this_q), this_q]
    actuals = await quarter_actuals(db, user.id, periods)
    budgets = await quarter_budgets(db, user.id, periods)
    for period in periods:
        actual = actuals.get(period, Decimal(0))
        spend_pct = pct_of(actual, budgets.get(period))
        band = budget_band(spend_pct)
        if band not in ("red", "yellow") or spend_pct is None:
            continue
        item = _oc_item(
            title=f"Outside counsel spend {period}: {spend_pct}% of budget",
            why=f"${money(actual)} spent vs ${money(budgets[period])} budgeted "
            f"(watch at {BUDGET_YELLOW_PCT}%)",
            next_step="Review which firms drove it and re-forecast with Finance",
            firm_id=None,
            link=oc_link,
        )
        if band == "red":
            red.append(((1, 2, item.title), item))  # (k)
        else:
            yellow.append(((3, 0, item.title), item))  # (l)

    red.sort(key=lambda r: r[0])
    yellow.sort(key=lambda r: r[0])

    # Cap each band independently so a full red band cannot starve
    # yellow, reserving slots per source inside each band.
    red_items = _cap_with_reserved_slots(red, _MAX_RED_ITEMS)
    yellow_items = _cap_with_reserved_slots(yellow, _MAX_YELLOW_ITEMS)

    log.info(
        "urgent matters computed",
        extra={
            "event": "management_urgent_matters",
            "user_id": str(user.id),
            "red_count": len(red_items),
            "yellow_count": len(yellow_items),
        },
    )

    return UrgentMattersRead(generated_at=now, red=red_items, yellow=yellow_items)


__all__ = ["get_urgent_matters", "router"]
