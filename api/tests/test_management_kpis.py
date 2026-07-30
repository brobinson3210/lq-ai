"""Integration tests for the Management-tab KPIs module.

Covers:

* CRUD happy paths for team members, KPIs, and datapoints.
* Validation — scope/team_member pairing (422 mirroring the DB CHECK),
  period-format-vs-cadence (422), enum'd fields, name length, unknown
  fields, non-UUID path ids (400).
* Upsert-by-period — duplicate period 409; ``?overwrite=true`` updates
  in place (200) and audits as ``mgmt_kpi.datapoint_update``.
* Owner isolation — cross-user reads/writes return 404 everywhere so
  ids do not leak across users (same posture as stakeholders).
* Soft-delete semantics — DELETE sets ``deleted_at``; a deleted team
  member's individual KPIs are tombstoned with them and disappear from
  the list, detail, and dashboard surfaces; second DELETE returns 404.
* Computed fields — latest/previous values, datapoint_count, and
  attainment_pct in both directions (ratio inverted for
  lower_is_better).
* Series ordering (period ascending) and the ``from``/``to`` range
  filter on the datapoints list.
* Dashboard shape — department-scope KPIs grouped by department, team
  entries carrying each member's individual KPIs and kpi_count.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import app
from app.models import AuditLog, MgmtKpi, MgmtKpiDatapoint, MgmtTeamMember, User
from app.security import create_access_token, hash_password


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
        email=f"mgmt-kpi-{suffix or uuid.uuid4().hex[:8]}@example.com",
        display_name=f"Mgmt KPI User {suffix}".strip(),
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


async def _create_member(
    client: AsyncClient,
    user: User,
    *,
    name: str = "Dana Counsel",
    department: str = "legal",
    **extra: object,
) -> dict:
    body: dict[str, object] = {
        "name": name,
        "role_title": "Senior Counsel",
        "department": department,
        **extra,
    }
    resp = await client.post("/api/v1/management/team-members", headers=_bearer(user), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_kpi(
    client: AsyncClient,
    user: User,
    *,
    name: str = "Contract turnaround",
    department: str = "legal",
    scope: str = "department",
    **extra: object,
) -> dict:
    body: dict[str, object] = {
        "name": name,
        "department": department,
        "scope": scope,
        "unit": "days",
        "cadence": "monthly",
        "direction": "lower_is_better",
        **extra,
    }
    resp = await client.post("/api/v1/management/kpis", headers=_bearer(user), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_datapoint(
    client: AsyncClient,
    user: User,
    kpi_id: str,
    period: str,
    value: str,
    **extra: object,
) -> dict:
    resp = await client.post(
        f"/api/v1/management/kpis/{kpi_id}/datapoints",
        headers=_bearer(user),
        json={"period": period, "value": value, **extra},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _audit_rows(db_session: AsyncSession, user: User, action: str) -> list[AuditLog]:
    return list(
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == action,
                )
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Team-member CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_member_create_returns_201_persists_and_audits(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    body = {
        "name": "Priya Compliance",
        "role_title": "Compliance Manager",
        "department": "compliance",
        "seniority": "manager",
        "strengths_md": "- regulatory mapping",
        "development_areas_md": "- delegation",
        "notes_md": "Joined 2024.",
    }
    resp = await client.post("/api/v1/management/team-members", headers=_bearer(user_a), json=body)
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["name"] == "Priya Compliance"
    assert payload["department"] == "compliance"
    assert payload["owner_id"] == str(user_a.id)
    assert payload["deleted_at"] is None
    assert payload["kpi_count"] == 0

    persisted = (
        await db_session.execute(
            select(MgmtTeamMember).where(MgmtTeamMember.id == uuid.UUID(payload["id"]))
        )
    ).scalar_one()
    assert persisted.owner_id == user_a.id
    assert persisted.seniority == "manager"

    audits = await _audit_rows(db_session, user_a, "mgmt_team_member.create")
    assert len(audits) == 1
    assert audits[0].resource_id == payload["id"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad_payload",
    [
        {"name": "", "role_title": "GC", "department": "legal"},
        {"name": "x" * 201, "role_title": "GC", "department": "legal"},
        {"name": "Jo", "role_title": "GC", "department": "finance"},  # not a dept
        {"name": "Jo", "role_title": "", "department": "legal"},
        {"name": "Jo", "department": "legal"},  # missing role_title
        {"name": "Jo", "role_title": "GC", "department": "legal", "unknown_field": 1},
    ],
)
async def test_member_create_rejects_invalid_payloads(
    client: AsyncClient, user_a: User, bad_payload: dict
) -> None:
    resp = await client.post(
        "/api/v1/management/team-members", headers=_bearer(user_a), json=bad_payload
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.integration
async def test_member_get_patch_and_no_op_patch(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    created = await _create_member(client, user_a)

    got = await client.get(
        f"/api/v1/management/team-members/{created['id']}", headers=_bearer(user_a)
    )
    assert got.status_code == 200, got.text
    assert got.json()["id"] == created["id"]

    patched = await client.patch(
        f"/api/v1/management/team-members/{created['id']}",
        headers=_bearer(user_a),
        json={"seniority": "director", "notes_md": None},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["seniority"] == "director"
    assert patched.json()["name"] == created["name"]  # untouched

    audits = await _audit_rows(db_session, user_a, "mgmt_team_member.update")
    assert len(audits) == 1
    assert audits[0].details["changed_fields"] == ["seniority"]

    # No-op PATCH: no new audit row, updated_at untouched.
    noop = await client.patch(
        f"/api/v1/management/team-members/{created['id']}",
        headers=_bearer(user_a),
        json={"name": created["name"]},
    )
    assert noop.status_code == 200, noop.text
    audits = await _audit_rows(db_session, user_a, "mgmt_team_member.update")
    assert len(audits) == 1


@pytest.mark.integration
async def test_member_non_uuid_id_returns_400_and_unknown_404(
    client: AsyncClient, user_a: User
) -> None:
    resp = await client.get("/api/v1/management/team-members/not-a-uuid", headers=_bearer(user_a))
    assert resp.status_code == 400
    resp = await client.get(
        f"/api/v1/management/team-members/{uuid.uuid4()}", headers=_bearer(user_a)
    )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_endpoints_require_bearer(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/management/team-members")).status_code == 401
    assert (await client.get("/api/v1/management/kpis")).status_code == 401
    assert (await client.get("/api/v1/management/dashboard")).status_code == 401


# ---------------------------------------------------------------------------
# KPI CRUD + validation
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_kpi_create_returns_201_persists_and_audits(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    body = {
        "name": "Outside counsel spend",
        "department": "legal",
        "scope": "department",
        "unit": "USD",
        "cadence": "quarterly",
        "direction": "lower_is_better",
        "baseline": "1200000",
        "target": "900000",
        "rationale_md": "Spend discipline proves the in-house model.",
    }
    resp = await client.post("/api/v1/management/kpis", headers=_bearer(user_a), json=body)
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["owner_id"] == str(user_a.id)
    assert payload["baseline"] == "1200000"
    assert payload["target"] == "900000"
    assert payload["team_member_id"] is None
    # Computed fields on a fresh row.
    assert payload["latest_period"] is None
    assert payload["latest_value"] is None
    assert payload["previous_value"] is None
    assert payload["datapoint_count"] == 0
    assert payload["attainment_pct"] is None

    persisted = (
        await db_session.execute(select(MgmtKpi).where(MgmtKpi.id == uuid.UUID(payload["id"])))
    ).scalar_one()
    assert persisted.owner_id == user_a.id
    assert persisted.target == Decimal("900000")

    audits = await _audit_rows(db_session, user_a, "mgmt_kpi.create")
    assert len(audits) == 1
    assert audits[0].resource_id == payload["id"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "x" * 201},
        {"department": "hr"},
        {"scope": "team"},
        {"cadence": "weekly"},
        {"direction": "sideways"},
        {"unit": ""},
        {"unknown_field": 1},
        # scope/team_member pairing violations (mirrors the DB CHECK).
        {"scope": "individual"},  # individual without a member
    ],
)
async def test_kpi_create_rejects_invalid_payloads(
    client: AsyncClient, user_a: User, overrides: dict
) -> None:
    body = {
        "name": "Cycle time",
        "department": "legal",
        "scope": "department",
        "unit": "days",
        "cadence": "monthly",
        "direction": "lower_is_better",
        **overrides,
    }
    resp = await client.post("/api/v1/management/kpis", headers=_bearer(user_a), json=body)
    assert resp.status_code == 422, resp.text


@pytest.mark.integration
async def test_kpi_create_department_scope_with_member_is_422(
    client: AsyncClient, user_a: User
) -> None:
    member = await _create_member(client, user_a)
    resp = await client.post(
        "/api/v1/management/kpis",
        headers=_bearer(user_a),
        json={
            "name": "Cycle time",
            "department": "legal",
            "scope": "department",
            "team_member_id": member["id"],
            "unit": "days",
            "cadence": "monthly",
            "direction": "lower_is_better",
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.integration
async def test_kpi_create_individual_scope_binds_member(client: AsyncClient, user_a: User) -> None:
    member = await _create_member(client, user_a)
    kpi = await _create_kpi(
        client,
        user_a,
        name="NDAs closed",
        scope="individual",
        team_member_id=member["id"],
    )
    assert kpi["team_member_id"] == member["id"]

    # kpi_count on the member reflects the assignment.
    detail = await client.get(
        f"/api/v1/management/team-members/{member['id']}", headers=_bearer(user_a)
    )
    assert detail.json()["kpi_count"] == 1


@pytest.mark.integration
async def test_kpi_create_with_unknown_or_cross_user_member_is_404(
    client: AsyncClient, user_a: User, user_b: User
) -> None:
    theirs = await _create_member(client, user_b)
    for member_id in (str(uuid.uuid4()), theirs["id"]):
        resp = await client.post(
            "/api/v1/management/kpis",
            headers=_bearer(user_a),
            json={
                "name": "NDAs closed",
                "department": "legal",
                "scope": "individual",
                "team_member_id": member_id,
                "unit": "count",
                "cadence": "monthly",
                "direction": "higher_is_better",
            },
        )
        assert resp.status_code == 404, resp.text


@pytest.mark.integration
async def test_kpi_patch_updates_fields_and_enforces_scope_pairing(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    member = await _create_member(client, user_a)
    kpi = await _create_kpi(client, user_a)

    # Ordinary field update.
    patched = await client.patch(
        f"/api/v1/management/kpis/{kpi['id']}",
        headers=_bearer(user_a),
        json={"target": "5", "rationale_md": "Five-day turnaround."},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["target"] == "5"

    audits = await _audit_rows(db_session, user_a, "mgmt_kpi.update")
    assert len(audits) == 1
    assert set(audits[0].details["changed_fields"]) == {"target", "rationale_md"}

    # scope -> individual without a member: 422 on the resulting state.
    resp = await client.patch(
        f"/api/v1/management/kpis/{kpi['id']}",
        headers=_bearer(user_a),
        json={"scope": "individual"},
    )
    assert resp.status_code == 422, resp.text

    # member without scope change (still department): 422.
    resp = await client.patch(
        f"/api/v1/management/kpis/{kpi['id']}",
        headers=_bearer(user_a),
        json={"team_member_id": member["id"]},
    )
    assert resp.status_code == 422, resp.text

    # Valid combined move to individual.
    resp = await client.patch(
        f"/api/v1/management/kpis/{kpi['id']}",
        headers=_bearer(user_a),
        json={"scope": "individual", "team_member_id": member["id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["team_member_id"] == member["id"]

    # Reassigning to another user's member: 404, row untouched.
    theirs = await _create_member(client, user_b)
    resp = await client.patch(
        f"/api/v1/management/kpis/{kpi['id']}",
        headers=_bearer(user_a),
        json={"team_member_id": theirs["id"]},
    )
    assert resp.status_code == 404, resp.text
    detail = await client.get(f"/api/v1/management/kpis/{kpi['id']}", headers=_bearer(user_a))
    assert detail.json()["team_member_id"] == member["id"]

    # No-op PATCH skips audit.
    before = len(await _audit_rows(db_session, user_a, "mgmt_kpi.update"))
    resp = await client.patch(
        f"/api/v1/management/kpis/{kpi['id']}",
        headers=_bearer(user_a),
        json={"target": "5"},
    )
    assert resp.status_code == 200, resp.text
    assert len(await _audit_rows(db_session, user_a, "mgmt_kpi.update")) == before


@pytest.mark.integration
async def test_kpi_list_filters(client: AsyncClient, user_a: User) -> None:
    member = await _create_member(client, user_a, department="compliance")
    await _create_kpi(client, user_a, name="Legal dept KPI", department="legal")
    await _create_kpi(client, user_a, name="Compliance dept KPI", department="compliance")
    await _create_kpi(
        client,
        user_a,
        name="Individual KPI",
        department="compliance",
        scope="individual",
        team_member_id=member["id"],
    )

    listed = await client.get("/api/v1/management/kpis", headers=_bearer(user_a))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 3

    by_dept = await client.get(
        "/api/v1/management/kpis", headers=_bearer(user_a), params={"department": "legal"}
    )
    assert [k["name"] for k in by_dept.json()] == ["Legal dept KPI"]

    by_scope = await client.get(
        "/api/v1/management/kpis", headers=_bearer(user_a), params={"scope": "individual"}
    )
    assert [k["name"] for k in by_scope.json()] == ["Individual KPI"]

    by_member = await client.get(
        "/api/v1/management/kpis",
        headers=_bearer(user_a),
        params={"team_member_id": member["id"]},
    )
    assert [k["name"] for k in by_member.json()] == ["Individual KPI"]

    # Bad filter values: domain 400s.
    assert (
        await client.get(
            "/api/v1/management/kpis", headers=_bearer(user_a), params={"department": "hr"}
        )
    ).status_code == 400
    assert (
        await client.get(
            "/api/v1/management/kpis", headers=_bearer(user_a), params={"scope": "global"}
        )
    ).status_code == 400
    assert (
        await client.get(
            "/api/v1/management/kpis",
            headers=_bearer(user_a),
            params={"team_member_id": "not-a-uuid"},
        )
    ).status_code == 400


# ---------------------------------------------------------------------------
# Owner isolation (cross-user 404)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_cross_user_access_returns_404_everywhere(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    member = await _create_member(client, user_b)
    kpi = await _create_kpi(client, user_b)
    await _add_datapoint(client, user_b, kpi["id"], "2026-06", "10")

    a = _bearer(user_a)
    mid, kid = member["id"], kpi["id"]
    assert (
        await client.get(f"/api/v1/management/team-members/{mid}", headers=a)
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/management/team-members/{mid}", headers=a, json={"name": "hijack"}
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/management/team-members/{mid}", headers=a)
    ).status_code == 404
    assert (await client.get(f"/api/v1/management/kpis/{kid}", headers=a)).status_code == 404
    assert (
        await client.patch(f"/api/v1/management/kpis/{kid}", headers=a, json={"name": "hijack"})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/management/kpis/{kid}", headers=a)).status_code == 404
    assert (
        await client.get(f"/api/v1/management/kpis/{kid}/datapoints", headers=a)
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/management/kpis/{kid}/datapoints",
            headers=a,
            json={"period": "2026-07", "value": "1"},
        )
    ).status_code == 404
    assert (await client.get(f"/api/v1/management/kpis/{kid}/series", headers=a)).status_code == 404

    # B's rows untouched by the failed writes.
    untouched_member = (
        await db_session.execute(select(MgmtTeamMember).where(MgmtTeamMember.id == uuid.UUID(mid)))
    ).scalar_one()
    assert untouched_member.name == member["name"]
    assert untouched_member.deleted_at is None
    untouched_kpi = (
        await db_session.execute(select(MgmtKpi).where(MgmtKpi.id == uuid.UUID(kid)))
    ).scalar_one()
    assert untouched_kpi.deleted_at is None

    # Lists and dashboard are owner-scoped.
    assert (await client.get("/api/v1/management/team-members", headers=a)).json() == []
    assert (await client.get("/api/v1/management/kpis", headers=a)).json() == []
    dashboard = (await client.get("/api/v1/management/dashboard", headers=a)).json()
    assert dashboard == {"departments": {"legal": [], "compliance": []}, "team": []}


# ---------------------------------------------------------------------------
# Datapoints: period validation, upsert, range filter
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_datapoint_period_must_match_cadence(client: AsyncClient, user_a: User) -> None:
    monthly = await _create_kpi(client, user_a, name="Monthly KPI")
    quarterly = await _create_kpi(client, user_a, name="Quarterly KPI", cadence="quarterly")

    # Monthly KPI accepts YYYY-MM only.
    for bad in ("2026-7", "2026-13", "2026-00", "2026-Q1", "202607", "jul-2026"):
        resp = await client.post(
            f"/api/v1/management/kpis/{monthly['id']}/datapoints",
            headers=_bearer(user_a),
            json={"period": bad, "value": "1"},
        )
        assert resp.status_code == 422, (bad, resp.text)
    await _add_datapoint(client, user_a, monthly["id"], "2026-07", "12.5")

    # Quarterly KPI accepts YYYY-Qn only.
    for bad in ("2026-05", "2026-Q5", "2026-Q0", "2026-q1"):
        resp = await client.post(
            f"/api/v1/management/kpis/{quarterly['id']}/datapoints",
            headers=_bearer(user_a),
            json={"period": bad, "value": "1"},
        )
        assert resp.status_code == 422, (bad, resp.text)
    created = await _add_datapoint(client, user_a, quarterly["id"], "2026-Q2", "3")
    assert created["value"] == "3"


@pytest.mark.integration
async def test_datapoint_duplicate_period_409_then_overwrite(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    kpi = await _create_kpi(client, user_a)
    first = await _add_datapoint(client, user_a, kpi["id"], "2026-06", "10", note_md="June.")
    assert first["value"] == "10"

    # Same period again: 409 without overwrite; row unchanged.
    resp = await client.post(
        f"/api/v1/management/kpis/{kpi['id']}/datapoints",
        headers=_bearer(user_a),
        json={"period": "2026-06", "value": "11"},
    )
    assert resp.status_code == 409, resp.text
    persisted = (
        await db_session.execute(
            select(MgmtKpiDatapoint).where(MgmtKpiDatapoint.id == uuid.UUID(first["id"]))
        )
    ).scalar_one()
    assert persisted.value == Decimal("10")

    # overwrite=true: updates in place (200, same row id) + audit.
    resp = await client.post(
        f"/api/v1/management/kpis/{kpi['id']}/datapoints",
        headers=_bearer(user_a),
        params={"overwrite": "true"},
        json={"period": "2026-06", "value": "11", "note_md": "June restated."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == first["id"]
    assert body["value"] == "11"
    assert body["note_md"] == "June restated."

    create_audits = await _audit_rows(db_session, user_a, "mgmt_kpi.datapoint_create")
    update_audits = await _audit_rows(db_session, user_a, "mgmt_kpi.datapoint_update")
    assert len(create_audits) == 1
    assert len(update_audits) == 1
    assert update_audits[0].resource_id == first["id"]

    # Only one row exists for the period.
    datapoints = await client.get(
        f"/api/v1/management/kpis/{kpi['id']}/datapoints", headers=_bearer(user_a)
    )
    assert len(datapoints.json()) == 1


@pytest.mark.integration
async def test_datapoint_range_filter_and_series_ordering(
    client: AsyncClient, user_a: User
) -> None:
    kpi = await _create_kpi(client, user_a)
    # Insert out of order; reads must come back period-ascending.
    for period, value in (("2026-03", "30"), ("2026-01", "10"), ("2026-02", "20")):
        await _add_datapoint(client, user_a, kpi["id"], period, value)

    listed = await client.get(
        f"/api/v1/management/kpis/{kpi['id']}/datapoints", headers=_bearer(user_a)
    )
    assert [d["period"] for d in listed.json()] == ["2026-01", "2026-02", "2026-03"]

    ranged = await client.get(
        f"/api/v1/management/kpis/{kpi['id']}/datapoints",
        headers=_bearer(user_a),
        params={"from": "2026-02", "to": "2026-03"},
    )
    assert [d["period"] for d in ranged.json()] == ["2026-02", "2026-03"]

    # Bounds must match the KPI's period format.
    bad = await client.get(
        f"/api/v1/management/kpis/{kpi['id']}/datapoints",
        headers=_bearer(user_a),
        params={"from": "2026-Q1"},
    )
    assert bad.status_code == 422, bad.text

    series = await client.get(
        f"/api/v1/management/kpis/{kpi['id']}/series", headers=_bearer(user_a)
    )
    assert series.status_code == 200, series.text
    body = series.json()
    assert body["kpi"]["id"] == kpi["id"]
    assert [p["period"] for p in body["datapoints"]] == ["2026-01", "2026-02", "2026-03"]
    assert [p["value"] for p in body["datapoints"]] == ["10", "20", "30"]


# ---------------------------------------------------------------------------
# Computed fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_computed_fields_and_attainment_higher_is_better(
    client: AsyncClient, user_a: User
) -> None:
    kpi = await _create_kpi(
        client,
        user_a,
        name="NDAs closed per month",
        direction="higher_is_better",
        target="100",
        baseline="60",
    )
    await _add_datapoint(client, user_a, kpi["id"], "2026-05", "80")
    await _add_datapoint(client, user_a, kpi["id"], "2026-06", "90")

    detail = (
        await client.get(f"/api/v1/management/kpis/{kpi['id']}", headers=_bearer(user_a))
    ).json()
    assert detail["latest_period"] == "2026-06"
    assert detail["latest_value"] == "90"
    assert detail["previous_value"] == "80"
    assert detail["datapoint_count"] == 2
    # higher_is_better: latest/target*100 = 90/100*100 = 90.0.
    assert detail["attainment_pct"] == "90.0"


@pytest.mark.integration
async def test_computed_attainment_lower_is_better_inverts_ratio(
    client: AsyncClient, user_a: User
) -> None:
    kpi = await _create_kpi(
        client,
        user_a,
        name="Contract turnaround",
        direction="lower_is_better",
        target="30",
    )
    await _add_datapoint(client, user_a, kpi["id"], "2026-06", "25")

    detail = (
        await client.get(f"/api/v1/management/kpis/{kpi['id']}", headers=_bearer(user_a))
    ).json()
    # lower_is_better beating target: target/latest*100 = 30/25*100 = 120.0.
    assert detail["attainment_pct"] == "120.0"

    # Missing the target (worse): 30/40*100 = 75.0.
    resp = await client.post(
        f"/api/v1/management/kpis/{kpi['id']}/datapoints",
        headers=_bearer(user_a),
        params={"overwrite": "true"},
        json={"period": "2026-06", "value": "40"},
    )
    assert resp.status_code == 200
    detail = (
        await client.get(f"/api/v1/management/kpis/{kpi['id']}", headers=_bearer(user_a))
    ).json()
    assert detail["attainment_pct"] == "75.0"


@pytest.mark.integration
async def test_attainment_null_without_target_or_on_zero_divisor(
    client: AsyncClient, user_a: User
) -> None:
    # No target: attainment is null even with datapoints.
    no_target = await _create_kpi(client, user_a, name="No target")
    await _add_datapoint(client, user_a, no_target["id"], "2026-06", "5")
    detail = (
        await client.get(f"/api/v1/management/kpis/{no_target['id']}", headers=_bearer(user_a))
    ).json()
    assert detail["attainment_pct"] is None
    assert detail["latest_value"] == "5"

    # lower_is_better with latest value 0: divisor is zero -> null.
    zero_div = await _create_kpi(
        client, user_a, name="Zero divisor", direction="lower_is_better", target="10"
    )
    await _add_datapoint(client, user_a, zero_div["id"], "2026-06", "0")
    detail = (
        await client.get(f"/api/v1/management/kpis/{zero_div['id']}", headers=_bearer(user_a))
    ).json()
    assert detail["attainment_pct"] is None

    # higher_is_better with target 0: divisor is zero -> null.
    zero_target = await _create_kpi(
        client, user_a, name="Zero target", direction="higher_is_better", target="0"
    )
    await _add_datapoint(client, user_a, zero_target["id"], "2026-06", "5")
    detail = (
        await client.get(f"/api/v1/management/kpis/{zero_target['id']}", headers=_bearer(user_a))
    ).json()
    assert detail["attainment_pct"] is None


@pytest.mark.integration
async def test_attainment_rounds_to_one_decimal(client: AsyncClient, user_a: User) -> None:
    kpi = await _create_kpi(
        client, user_a, name="Rounding", direction="higher_is_better", target="3"
    )
    await _add_datapoint(client, user_a, kpi["id"], "2026-06", "1")
    detail = (
        await client.get(f"/api/v1/management/kpis/{kpi['id']}", headers=_bearer(user_a))
    ).json()
    # 1/3*100 = 33.333... -> "33.3".
    assert detail["attainment_pct"] == "33.3"


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_kpi_soft_delete_hides_row_and_is_not_repeatable(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    kpi = await _create_kpi(client, user_a)
    await _add_datapoint(client, user_a, kpi["id"], "2026-06", "10")

    resp = await client.delete(f"/api/v1/management/kpis/{kpi['id']}", headers=_bearer(user_a))
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    persisted = (
        await db_session.execute(select(MgmtKpi).where(MgmtKpi.id == uuid.UUID(kpi["id"])))
    ).scalar_one()
    assert persisted.deleted_at is not None

    # Invisible via detail, list, datapoints, series.
    a = _bearer(user_a)
    assert (await client.get(f"/api/v1/management/kpis/{kpi['id']}", headers=a)).status_code == 404
    assert (await client.get("/api/v1/management/kpis", headers=a)).json() == []
    assert (
        await client.get(f"/api/v1/management/kpis/{kpi['id']}/datapoints", headers=a)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/management/kpis/{kpi['id']}/series", headers=a)
    ).status_code == 404

    # Datapoint rows survive the soft delete (CASCADE is hard-delete only).
    datapoints = (
        (
            await db_session.execute(
                select(MgmtKpiDatapoint).where(MgmtKpiDatapoint.kpi_id == uuid.UUID(kpi["id"]))
            )
        )
        .scalars()
        .all()
    )
    assert len(datapoints) == 1

    # Second delete: 404.
    assert (
        await client.delete(f"/api/v1/management/kpis/{kpi['id']}", headers=a)
    ).status_code == 404

    audits = await _audit_rows(db_session, user_a, "mgmt_kpi.delete")
    assert len(audits) == 1
    assert audits[0].resource_id == kpi["id"]


@pytest.mark.integration
async def test_member_soft_delete_tombstones_individual_kpis(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    member = await _create_member(client, user_a, name="Departing Counsel")
    individual = await _create_kpi(
        client,
        user_a,
        name="Individual KPI",
        scope="individual",
        team_member_id=member["id"],
    )
    department = await _create_kpi(client, user_a, name="Department KPI")

    # Pre-delete dashboard shows both surfaces.
    dashboard = (await client.get("/api/v1/management/dashboard", headers=_bearer(user_a))).json()
    assert [e["member"]["id"] for e in dashboard["team"]] == [member["id"]]
    assert [k["id"] for k in dashboard["team"][0]["kpis"]] == [individual["id"]]

    resp = await client.delete(
        f"/api/v1/management/team-members/{member['id']}", headers=_bearer(user_a)
    )
    assert resp.status_code == 204, resp.text

    # Member and their individual KPI vanish from every surface; the
    # department KPI is untouched.
    a = _bearer(user_a)
    assert (
        await client.get(f"/api/v1/management/team-members/{member['id']}", headers=a)
    ).status_code == 404
    assert (await client.get("/api/v1/management/team-members", headers=a)).json() == []
    assert (
        await client.get(f"/api/v1/management/kpis/{individual['id']}", headers=a)
    ).status_code == 404
    assert [k["id"] for k in (await client.get("/api/v1/management/kpis", headers=a)).json()] == [
        department["id"]
    ]

    dashboard = (await client.get("/api/v1/management/dashboard", headers=a)).json()
    assert dashboard["team"] == []
    assert [k["id"] for k in dashboard["departments"]["legal"]] == [department["id"]]

    # The tombstone is set on both rows; audit carries the cascade count.
    persisted_kpi = (
        await db_session.execute(select(MgmtKpi).where(MgmtKpi.id == uuid.UUID(individual["id"])))
    ).scalar_one()
    assert persisted_kpi.deleted_at is not None
    audits = await _audit_rows(db_session, user_a, "mgmt_team_member.delete")
    assert len(audits) == 1
    assert audits[0].details["kpis_tombstoned"] == 1

    # Second delete: 404.
    assert (
        await client.delete(f"/api/v1/management/team-members/{member['id']}", headers=a)
    ).status_code == 404


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dashboard_shape_and_grouping(
    client: AsyncClient, user_a: User, user_b: User
) -> None:
    alice = await _create_member(client, user_a, name="Alice", department="legal")
    await _create_member(client, user_a, name="Bob", department="compliance")
    await _create_member(client, user_b, name="Not Mine")

    legal_kpi = await _create_kpi(client, user_a, name="Legal turnaround", department="legal")
    compliance_kpi = await _create_kpi(
        client, user_a, name="Training completion", department="compliance"
    )
    alice_kpi = await _create_kpi(
        client,
        user_a,
        name="Alice NDAs",
        department="legal",
        scope="individual",
        team_member_id=alice["id"],
        direction="higher_is_better",
        target="10",
    )
    await _create_kpi(client, user_b, name="B's KPI")
    await _add_datapoint(client, user_a, alice_kpi["id"], "2026-06", "12")

    resp = await client.get("/api/v1/management/dashboard", headers=_bearer(user_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # departments: scope='department' only, grouped correctly.
    assert [k["id"] for k in body["departments"]["legal"]] == [legal_kpi["id"]]
    assert [k["id"] for k in body["departments"]["compliance"]] == [compliance_kpi["id"]]

    # team: every active member (name asc), with their individual KPIs
    # and computed fields batched in.
    assert [e["member"]["name"] for e in body["team"]] == ["Alice", "Bob"]
    alice_entry, bob_entry = body["team"]
    assert alice_entry["member"]["kpi_count"] == 1
    assert [k["id"] for k in alice_entry["kpis"]] == [alice_kpi["id"]]
    assert alice_entry["kpis"][0]["latest_value"] == "12"
    assert alice_entry["kpis"][0]["attainment_pct"] == "120.0"
    assert bob_entry["kpis"] == []
    assert bob_entry["member"]["kpi_count"] == 0

    # The individual KPI does not leak into the departments grouping.
    assert alice_kpi["id"] not in [k["id"] for k in body["departments"]["legal"]]
