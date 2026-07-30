"""Urgent Matters endpoint — Management tab, "Urgent Matters" button.

Surface:

* ``GET /api/v1/management/urgent-matters`` — one computed, read-only
  call that answers "what needs my attention right now". No tables of
  its own; it scans three existing sources and buckets findings into
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
  ("urgent for them").

**YELLOW** = any of:

* (d) any other open commitment due within 5 days;
* (e) a regulator/auditor commitment 8-14 days out (the extended
  visibility window — on the radar, not yet red);
* (f) a stakeholder cadence breach;
* (g) a department-scope KPI at or below 80% attainment (behind, but
  not same-day);
* (h) an individual-scope KPI at or below 80% attainment, titled with
  the team member's name.

**Ranking.** Red first — most-overdue first (most-negative
``days_until_due``), then soonest due. Then yellow — commitments by
due date, then cadence breaches by days-over (largest gap first), then
KPIs by attainment ascending.

**Caps.** Each band is capped independently: up to 10 reds and up to 5
yellows. A single combined cap of 10 starved the yellow band entirely
whenever there were ten reds — the band promises "needs your attention
in the next 5 days" and must not go dark just because the reds are
busy (Bill's decision 2026-07-30, superseding the round-3 combined
cap).

**Per-user isolation.** Owner-scoped exactly as the sibling Management
modules: every query filters on ``owner_id``; soft-deleted rows never
contribute. Read-only — no audit rows. Three batched queries total
(commitments+stakeholders joined, stakeholders with the cadence
aggregate, KPIs with the latest-value aggregate) — never N+1.
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
from app.db.session import get_db
from app.models.management_kpi import MgmtKpi, MgmtKpiDatapoint, MgmtTeamMember
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
# whose first element sequences the yellow sub-groups (0 = commitments,
# 1 = cadence breaches, 2 = KPIs); reds sort purely by urgency.
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
        "stakeholder cadence breaches, and KPIs at or below 80% "
        "attainment, and buckets findings into ``red`` / ``yellow`` per "
        "the triage rule set (overdue commitments and regulator/board-"
        "chair/CEO commitments inside a week are red; other commitments "
        "due in the next 5 days, regulator items 8-14 days out, cadence "
        "breaches, and below-green KPIs are yellow). Each band is capped "
        "independently: up to 10 reds and up to 5 yellows, so a full red "
        "band never starves the yellow one. Documents "
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

    red.sort(key=lambda r: r[0])
    yellow.sort(key=lambda r: r[0])

    # Cap each band independently so a full red band cannot starve yellow.
    red_items = [item for _, item in red[:_MAX_RED_ITEMS]]
    yellow_items = [item for _, item in yellow[:_MAX_YELLOW_ITEMS]]

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
