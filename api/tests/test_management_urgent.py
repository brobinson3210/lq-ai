"""Integration tests for the Management-tab Urgent Matters endpoint.

``GET /api/v1/management/urgent-matters`` — the computed red/yellow
triage feed. Covers:

* Each RED rule: (a) overdue commitment, (b) regulator/auditor/board
  chair/CEO within 7 days, (c) C-suite peer within 3 days.
* Each YELLOW rule: (d) other commitments within 5 days, (e)
  regulator/auditor 8-14 days out, (f) cadence breaches, (g)
  department-scope KPIs at/below 80% attainment, (h) individual-scope
  KPIs at/below 80% titled with the member name.
* Horizon boundaries — 6 days out is excluded for a normal
  stakeholder (the yellow band promises "the next 5 days"), visible
  (yellow) up to 14 for a regulator, excluded at 15.
* The per-band caps (10 red / 5 yellow), including that a full red
  band does not starve the yellow one.
* Ranking order inside both buckets.
* Owner isolation and the empty state.

Dates are frozen relative to ``date.today()`` by inserting known
``due_date`` offsets, so assertions never depend on the wall clock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import app
from app.models import User
from app.security import create_access_token, hash_password

URGENT_URL = "/api/v1/management/urgent-matters"


def _override_get_db(db_session: AsyncSession):
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    return _override


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


async def _make_user(db_session: AsyncSession, *, suffix: str = "") -> User:
    user = User(
        email=f"urgent-{suffix or uuid.uuid4().hex[:8]}@example.com",
        display_name=f"Urgent User {suffix}".strip(),
        hashed_password=hash_password("correct-horse-battery-staple"),
        is_admin=False,
        mfa_enabled=False,
        must_change_password=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def user_a(db_session: AsyncSession) -> User:
    return await _make_user(db_session, suffix="a")


@pytest_asyncio.fixture
async def user_b(db_session: AsyncSession) -> User:
    return await _make_user(db_session, suffix="b")


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.email, is_admin=user.is_admin)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seed helpers (all through the public API)
# ---------------------------------------------------------------------------


async def _create_stakeholder(
    client: AsyncClient,
    user: User,
    *,
    full_name: str = "Jane Director",
    stakeholder_type: str = "director",
    **extra: object,
) -> dict:
    resp = await client.post(
        "/api/v1/stakeholders",
        headers=_bearer(user),
        json={"full_name": full_name, "stakeholder_type": stakeholder_type, **extra},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_commitment(
    client: AsyncClient,
    user: User,
    stakeholder_id: str,
    *,
    days_from_today: int | None,
    direction: str = "we_owe",
    description: str = "Send the board deck",
    status: str = "open",
) -> dict:
    body: dict[str, object] = {
        "direction": direction,
        "description": description,
        "status": status,
    }
    if days_from_today is not None:
        body["due_date"] = (date.today() + timedelta(days=days_from_today)).isoformat()
    resp = await client.post(
        f"/api/v1/stakeholders/{stakeholder_id}/commitments",
        headers=_bearer(user),
        json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_interaction(
    client: AsyncClient, user: User, stakeholder_id: str, *, days_ago: int
) -> None:
    occurred_at = (datetime.now(tz=UTC) - timedelta(days=days_ago)).isoformat()
    resp = await client.post(
        f"/api/v1/stakeholders/{stakeholder_id}/interactions",
        headers=_bearer(user),
        json={"occurred_at": occurred_at, "channel": "call", "summary_md": "Caught up."},
    )
    assert resp.status_code == 201, resp.text


async def _create_member(client: AsyncClient, user: User, *, name: str = "Dana Lee") -> dict:
    resp = await client.post(
        "/api/v1/management/team-members",
        headers=_bearer(user),
        json={"name": name, "role_title": "Counsel", "department": "legal"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_kpi_with_value(
    client: AsyncClient,
    user: User,
    *,
    name: str = "Contract turnaround",
    scope: str = "department",
    team_member_id: str | None = None,
    target: float = 100,
    latest_value: float | None = None,
    direction: str = "higher_is_better",
) -> dict:
    body: dict[str, object] = {
        "name": name,
        "department": "legal",
        "scope": scope,
        "unit": "count",
        "cadence": "monthly",
        "direction": direction,
        "target": target,
    }
    if team_member_id is not None:
        body["team_member_id"] = team_member_id
    resp = await client.post("/api/v1/management/kpis", headers=_bearer(user), json=body)
    assert resp.status_code == 201, resp.text
    kpi = resp.json()
    if latest_value is not None:
        dp = await client.post(
            f"/api/v1/management/kpis/{kpi['id']}/datapoints",
            headers=_bearer(user),
            json={"period": "2026-06", "value": latest_value},
        )
        assert dp.status_code == 201, dp.text
    return kpi


async def _get_urgent(client: AsyncClient, user: User) -> dict:
    resp = await client.get(URGENT_URL, headers=_bearer(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Empty state + auth
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_empty_state(client: AsyncClient, user_a: User) -> None:
    body = await _get_urgent(client, user_a)
    assert body["red"] == []
    assert body["yellow"] == []
    assert body["generated_at"] is not None


@pytest.mark.integration
async def test_requires_bearer(client: AsyncClient) -> None:
    resp = await client.get(URGENT_URL)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# RED rules (a)-(d)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_red_a_overdue_commitment(client: AsyncClient, user_a: User) -> None:
    """(a) An open commitment past due is red for any stakeholder type."""

    s = await _create_stakeholder(client, user_a, full_name="Olive Ordinary")
    await _add_commitment(client, user_a, s["id"], days_from_today=-2, description="Q2 memo")

    body = await _get_urgent(client, user_a)
    assert len(body["red"]) == 1
    item = body["red"][0]
    assert item["kind"] == "commitment"
    assert item["days_until_due"] == -2
    assert item["title"] == "Deliver: Q2 memo"
    assert "2 days overdue" in item["why"]
    assert "owed to Olive Ordinary (Director)" in item["why"]
    assert item["next_step"] == "Deliver or renegotiate the date"
    assert item["stakeholder_id"] == s["id"]
    assert item["stakeholder_name"] == "Olive Ordinary"
    assert item["kpi_id"] is None
    assert item["link"] == f"/lq-ai/management/stakeholders/{s['id']}"
    assert body["yellow"] == []


@pytest.mark.integration
async def test_red_a_done_or_dropped_commitments_ignored(client: AsyncClient, user_a: User) -> None:
    s = await _create_stakeholder(client, user_a)
    await _add_commitment(client, user_a, s["id"], days_from_today=-5, status="done")
    await _add_commitment(client, user_a, s["id"], days_from_today=-5, status="dropped")
    # Undated open commitments cannot be judged against the horizon.
    await _add_commitment(client, user_a, s["id"], days_from_today=None)

    body = await _get_urgent(client, user_a)
    assert body["red"] == []
    assert body["yellow"] == []


@pytest.mark.integration
@pytest.mark.parametrize("stakeholder_type", ["regulator", "auditor"])
async def test_red_b_regulator_auditor_red_week_yellow_fortnight(
    client: AsyncClient, user_a: User, stakeholder_type: str
) -> None:
    """(b)/(e) Regulator/auditor: red inside a week, yellow at 8-14 days.

    Bill's round-3 rule: red is what "a regulator requires immediately
    or less than one week"; 8-14 days out stays on the radar as yellow.
    """

    s = await _create_stakeholder(
        client, user_a, full_name="Rita Reg", stakeholder_type=stakeholder_type
    )
    await _add_commitment(client, user_a, s["id"], days_from_today=5, description="This week")
    await _add_commitment(client, user_a, s["id"], days_from_today=10, description="Next week")

    body = await _get_urgent(client, user_a)
    assert [i["title"] for i in body["red"]] == ["Deliver: This week"]
    assert [i["title"] for i in body["yellow"]] == ["Deliver: Next week"]


@pytest.mark.integration
@pytest.mark.parametrize("stakeholder_type", ["board_chair", "ceo"])
async def test_red_b_board_chair_ceo_within_7_days(
    client: AsyncClient, user_a: User, stakeholder_type: str
) -> None:
    """(b) Board chair / CEO commitments due within a week are red."""

    s = await _create_stakeholder(
        client, user_a, full_name="Margo Delclos", stakeholder_type=stakeholder_type
    )
    await _add_commitment(client, user_a, s["id"], days_from_today=7)

    body = await _get_urgent(client, user_a)
    assert [i["days_until_due"] for i in body["red"]] == [7]
    assert body["yellow"] == []


@pytest.mark.integration
async def test_red_c_c_suite_peer_within_3_days(client: AsyncClient, user_a: User) -> None:
    """(c) C-suite peer within 3 days is red ("urgent for them"); 4 days is yellow."""

    s = await _create_stakeholder(
        client, user_a, full_name="Pat Peer", stakeholder_type="c_suite_peer"
    )
    await _add_commitment(client, user_a, s["id"], days_from_today=3, description="Red one")
    await _add_commitment(client, user_a, s["id"], days_from_today=4, description="Yellow one")

    body = await _get_urgent(client, user_a)
    assert [i["title"] for i in body["red"]] == ["Deliver: Red one"]
    assert [i["title"] for i in body["yellow"]] == ["Deliver: Yellow one"]


# ---------------------------------------------------------------------------
# YELLOW rules (e)-(h)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_yellow_d_other_commitment_within_5_days(client: AsyncClient, user_a: User) -> None:
    """(d) A normal stakeholder's commitment due in the next 5 days is yellow, not red."""

    s = await _create_stakeholder(
        client, user_a, full_name="Lena Lender", stakeholder_type="lender"
    )
    await _add_commitment(
        client,
        user_a,
        s["id"],
        days_from_today=5,
        direction="they_owe",
        description="Countersigned amendment",
    )

    body = await _get_urgent(client, user_a)
    assert body["red"] == []
    assert len(body["yellow"]) == 1
    item = body["yellow"][0]
    assert item["title"] == "Chase: Countersigned amendment"
    assert "owed by Lena Lender (Lender)" in item["why"]
    assert "5 days from now" in item["why"]
    assert item["next_step"] == "Chase delivery or renegotiate the date"


@pytest.mark.integration
async def test_yellow_f_cadence_breach(client: AsyncClient, user_a: User) -> None:
    """(f) days_since_last_interaction > cadence_target_days flags the stakeholder."""

    breached = await _create_stakeholder(
        client, user_a, full_name="Bela Board", cadence_target_days=21
    )
    await _add_interaction(client, user_a, breached["id"], days_ago=40)

    within = await _create_stakeholder(
        client, user_a, full_name="Casey Current", cadence_target_days=30
    )
    await _add_interaction(client, user_a, within["id"], days_ago=10)

    # No cadence target -> never flagged, however stale.
    no_cadence = await _create_stakeholder(client, user_a, full_name="Nora NoCadence")
    await _add_interaction(client, user_a, no_cadence["id"], days_ago=100)

    # Cadence target but no interactions -> never flagged.
    await _create_stakeholder(client, user_a, full_name="Ivan Introless", cadence_target_days=7)

    body = await _get_urgent(client, user_a)
    assert body["red"] == []
    assert len(body["yellow"]) == 1
    item = body["yellow"][0]
    assert item["kind"] == "cadence"
    assert item["title"] == "Reconnect with Bela Board"
    assert item["why"] == "No touch in 40 days vs a 21-day target"
    assert item["next_step"] == "Schedule a touchpoint"
    assert item["due_date"] is None
    assert item["days_until_due"] is None
    assert item["stakeholder_id"] == breached["id"]
    assert item["link"] == f"/lq-ai/management/stakeholders/{breached['id']}"


@pytest.mark.integration
async def test_yellow_g_department_kpi_below_green_band(client: AsyncClient, user_a: User) -> None:
    """(g) A department KPI at <= 80% attainment surfaces as yellow."""

    flagged = await _create_kpi_with_value(
        client, user_a, name="Contracts closed", target=100, latest_value=43
    )
    # Exactly 80% is inside the band (<=), matching the green rule's edge.
    await _create_kpi_with_value(client, user_a, name="Trainings run", target=10, latest_value=8)
    # 81% is outside — green, not urgent.
    await _create_kpi_with_value(client, user_a, name="Audits done", target=100, latest_value=81)
    # No datapoints -> no attainment -> never flagged.
    await _create_kpi_with_value(client, user_a, name="No data yet", target=100, latest_value=None)

    body = await _get_urgent(client, user_a)
    assert body["red"] == []
    titles = [i["title"] for i in body["yellow"]]
    assert titles == ["Contracts closed", "Trainings run"]  # worst attainment first
    item = body["yellow"][0]
    assert item["kind"] == "kpi"
    assert item["why"] == "Attainment 43.0% of target"
    assert item["next_step"] == "Review the trend and the plan with the owner"
    assert item["kpi_id"] == flagged["id"]
    assert item["stakeholder_id"] is None
    assert item["link"] == f"/lq-ai/management/kpis/{flagged['id']}"


@pytest.mark.integration
async def test_yellow_g_lower_is_better_orientation(client: AsyncClient, user_a: User) -> None:
    """lower_is_better attainment is target/latest — 40% here flags the KPI."""

    await _create_kpi_with_value(
        client,
        user_a,
        name="Cycle time",
        target=10,
        latest_value=25,
        direction="lower_is_better",
    )
    body = await _get_urgent(client, user_a)
    assert [i["why"] for i in body["yellow"]] == ["Attainment 40.0% of target"]


@pytest.mark.integration
async def test_yellow_h_individual_kpi_titled_with_member_name(
    client: AsyncClient, user_a: User
) -> None:
    """(h) Individual-scope KPIs at <= 50% are yellow, titled with the member."""

    member = await _create_member(client, user_a, name="Dana Lee")
    await _create_kpi_with_value(
        client,
        user_a,
        name="Playbooks shipped",
        scope="individual",
        team_member_id=member["id"],
        target=4,
        latest_value=1,
    )
    body = await _get_urgent(client, user_a)
    assert [i["title"] for i in body["yellow"]] == ["Dana Lee: Playbooks shipped"]


# ---------------------------------------------------------------------------
# Horizon boundaries
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_horizon_boundaries(client: AsyncClient, user_a: User) -> None:
    """6 days out is invisible for a normal stakeholder; regulators reach 14 (yellow).

    The ordinary yellow band is exactly 5 days because the panel is
    titled "needs your attention in the next 5 days" (Bill's notes
    2026-07-30) — day 5 is in, day 6 is out.
    """

    normal = await _create_stakeholder(client, user_a, full_name="Norm Normal")
    await _add_commitment(client, user_a, normal["id"], days_from_today=6, description="Too far")
    await _add_commitment(client, user_a, normal["id"], days_from_today=5, description="In window")

    regulator = await _create_stakeholder(
        client, user_a, full_name="Rita Reg", stakeholder_type="regulator"
    )
    await _add_commitment(client, user_a, regulator["id"], days_from_today=14, description="Edge")
    await _add_commitment(
        client, user_a, regulator["id"], days_from_today=15, description="Past edge"
    )

    body = await _get_urgent(client, user_a)
    red_titles = [i["title"] for i in body["red"]]
    yellow_titles = [i["title"] for i in body["yellow"]]
    assert red_titles == []  # nothing inside a week
    # Normal at exactly 5 days, then the regulator's 14-day edge (still
    # yellow — regulator red means inside a week, per Bill's round 3).
    assert yellow_titles == ["Deliver: In window", "Deliver: Edge"]
    assert "Deliver: Too far" not in yellow_titles
    assert "Deliver: Past edge" not in yellow_titles


# ---------------------------------------------------------------------------
# Ranking + cap
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ranking_order(client: AsyncClient, user_a: User) -> None:
    """Reds by urgency; yellows commitments -> cadence -> KPIs, each sorted."""

    chair = await _create_stakeholder(
        client, user_a, full_name="Margo Delclos", stakeholder_type="board_chair"
    )
    plain = await _create_stakeholder(client, user_a, full_name="Pia Plain")

    # Reds: overdue -5 and -1 (any type), then chair due in +2.
    await _add_commitment(client, user_a, plain["id"], days_from_today=-1, description="Minus one")
    await _add_commitment(client, user_a, plain["id"], days_from_today=-5, description="Minus five")
    await _add_commitment(client, user_a, chair["id"], days_from_today=2, description="Chair soon")

    # Yellows: two commitments (+5, +4), two cadence breaches (10 and 30
    # days over), two KPIs (20% and 45% attainment).
    await _add_commitment(client, user_a, plain["id"], days_from_today=5, description="Later")
    await _add_commitment(client, user_a, plain["id"], days_from_today=4, description="Sooner")

    small_gap = await _create_stakeholder(
        client, user_a, full_name="Gail SmallGap", cadence_target_days=20
    )
    await _add_interaction(client, user_a, small_gap["id"], days_ago=30)
    big_gap = await _create_stakeholder(
        client, user_a, full_name="Bora BigGap", cadence_target_days=20
    )
    await _add_interaction(client, user_a, big_gap["id"], days_ago=50)

    await _create_kpi_with_value(client, user_a, name="Milder", target=100, latest_value=45)
    await _create_kpi_with_value(client, user_a, name="Worse", target=100, latest_value=20)

    body = await _get_urgent(client, user_a)
    assert [i["title"] for i in body["red"]] == [
        "Deliver: Minus five",
        "Deliver: Minus one",
        "Deliver: Chair soon",
    ]
    # Six yellows are constructed; the yellow cap is 5, so the mildest KPI
    # ("Milder", ranked last) falls off the bottom. Ordering across all
    # three sub-groups is still asserted by what survives.
    assert [i["title"] for i in body["yellow"]] == [
        "Deliver: Sooner",
        "Deliver: Later",
        "Reconnect with Bora BigGap",
        "Reconnect with Gail SmallGap",
        "Worse",
    ]


@pytest.mark.integration
async def test_bands_are_capped_independently(client: AsyncClient, user_a: User) -> None:
    """4 reds + 8 yellows -> all 4 reds and the 5 worst yellows.

    Each band has its own cap (10 red / 5 yellow). Under the old combined
    cap of 10 this returned 6 yellows; the point of the split is that the
    yellow band keeps its slots no matter how busy the reds are.
    """

    s = await _create_stakeholder(client, user_a, full_name="Cap Subject")
    for n in range(1, 5):
        await _add_commitment(client, user_a, s["id"], days_from_today=-n, description=f"Red {n}")
    for n in range(8):
        await _create_kpi_with_value(
            client, user_a, name=f"KPI {n}", target=100, latest_value=10 + n
        )

    body = await _get_urgent(client, user_a)
    assert len(body["red"]) == 4
    assert len(body["yellow"]) == 5
    assert [i["title"] for i in body["red"]] == [
        "Deliver: Red 4",
        "Deliver: Red 3",
        "Deliver: Red 2",
        "Deliver: Red 1",
    ]
    # Worst attainment first; the three mildest KPIs fell off the cap.
    assert [i["title"] for i in body["yellow"]] == [f"KPI {n}" for n in range(5)]


@pytest.mark.integration
async def test_full_red_band_does_not_starve_yellow(client: AsyncClient, user_a: User) -> None:
    """10 reds still leave room for yellows — the regression Bill hit 2026-07-30.

    Under the old combined cap of 10, a demo universe with ten overdue
    commitments rendered an empty yellow band, so the "next 5 days"
    section never appeared at all.
    """

    s = await _create_stakeholder(client, user_a, full_name="Busy Desk")
    for n in range(1, 11):
        await _add_commitment(client, user_a, s["id"], days_from_today=-n, description=f"Red {n}")
    for n in range(3):
        await _create_kpi_with_value(
            client, user_a, name=f"Slipping {n}", target=100, latest_value=10 + n
        )

    body = await _get_urgent(client, user_a)
    assert len(body["red"]) == 10
    assert [i["title"] for i in body["yellow"]] == [f"Slipping {n}" for n in range(3)]


# ---------------------------------------------------------------------------
# Isolation + hygiene
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_owner_isolation(client: AsyncClient, user_a: User, user_b: User) -> None:
    """B's urgent world never leaks into A's feed."""

    s_b = await _create_stakeholder(client, user_b, full_name="Belongs ToB", cadence_target_days=7)
    await _add_commitment(client, user_b, s_b["id"], days_from_today=-3)
    await _add_interaction(client, user_b, s_b["id"], days_ago=30)
    await _create_kpi_with_value(client, user_b, name="B KPI", target=100, latest_value=10)

    body_a = await _get_urgent(client, user_a)
    assert body_a["red"] == []
    assert body_a["yellow"] == []

    body_b = await _get_urgent(client, user_b)
    assert len(body_b["red"]) == 1
    assert len(body_b["yellow"]) == 2


@pytest.mark.integration
async def test_soft_deleted_sources_excluded(client: AsyncClient, user_a: User) -> None:
    """Soft-deleted stakeholders and KPIs contribute nothing."""

    s = await _create_stakeholder(client, user_a, full_name="Gone Soon", cadence_target_days=7)
    await _add_commitment(client, user_a, s["id"], days_from_today=-1)
    await _add_interaction(client, user_a, s["id"], days_ago=30)
    kpi = await _create_kpi_with_value(client, user_a, name="Doomed", target=100, latest_value=10)

    assert (
        await client.delete(f"/api/v1/stakeholders/{s['id']}", headers=_bearer(user_a))
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/management/kpis/{kpi['id']}", headers=_bearer(user_a))
    ).status_code == 204

    body = await _get_urgent(client, user_a)
    assert body["red"] == []
    assert body["yellow"] == []


@pytest.mark.integration
async def test_long_description_trimmed_in_title(client: AsyncClient, user_a: User) -> None:
    s = await _create_stakeholder(client, user_a)
    long_desc = "Prepare the comprehensive regulatory response package " * 4  # > 80 chars
    await _add_commitment(client, user_a, s["id"], days_from_today=-1, description=long_desc)

    body = await _get_urgent(client, user_a)
    title = body["red"][0]["title"]
    assert title.startswith("Deliver: Prepare the comprehensive")
    assert title.endswith("…")
    assert len(title) <= len("Deliver: ") + 80
