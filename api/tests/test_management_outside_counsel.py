"""Integration tests for the Management-tab Outside Counsel module.

Covers:

* CRUD happy paths — firms, chosen partners, budgets (upsert), line-item
  invoices (totals computed from lines; PATCH ``lines`` replaces them;
  period derived from the invoice date), and value-ledger entries.
* Validation — the value ledger's mandatory method note and source,
  discount range, unknown fields, empty invoices, non-UUID ids (400).
* Owner isolation — cross-user reads/writes return 404 everywhere.
* Soft delete — deleted firms drop out of lists and spend totals.
* GC policy rules — the staffing flag (2 billers on one task/day is
  allowed, 3 is flagged; a different day or task is not), discount
  floor / increase cap flags, budget bands (110% yellow, 125% red).
* Urgent Matters source 4 — departed partner red until resolved;
  discount below 10% red; increase above 5% yellow; quarter spend
  110-124% yellow and 125%+ red.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.management_outside_counsel import normalise_task, period_for
from app.db.session import get_db
from app.main import app
from app.models import AuditLog, Stakeholder, User
from app.security import create_access_token, hash_password

BASE = "/api/v1/management/outside-counsel"
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


async def _make_user(db_session: AsyncSession, *, suffix: str) -> User:
    user = User(
        email=f"mgmt-oc-{suffix}-{uuid.uuid4().hex[:6]}@example.com",
        display_name=f"Mgmt OC User {suffix}",
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


async def _firm(client: AsyncClient, user: User, **body: object) -> dict:
    payload: dict[str, object] = {"name": "Hartwell & Crane LLP", **body}
    resp = await client.post(f"{BASE}/firms", headers=_bearer(user), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _line(
    who: str,
    *,
    title: str = "associate",
    task: str = "Call with client re term sheet",
    day: date | None = None,
    hours: str = "1.0",
    rate: str = "800",
) -> dict[str, object]:
    return {
        "work_date": (day or date(2026, 2, 3)).isoformat(),
        "timekeeper": who,
        "title": title,
        "task": task,
        "hours": hours,
        "rate": rate,
    }


async def _invoice(
    client: AsyncClient,
    user: User,
    firm_id: str,
    *,
    lines: list[dict[str, object]] | None = None,
    invoice_date: date | None = None,
    practice_area: str = "corporate",
    **extra: object,
) -> dict:
    payload: dict[str, object] = {
        "firm_id": firm_id,
        "invoice_number": "INV-1",
        "invoice_date": (invoice_date or date(2026, 2, 28)).isoformat(),
        "practice_area": practice_area,
        "lines": lines or [_line("Aaron Feldstein")],
        **extra,
    }
    resp = await client.post(f"{BASE}/invoices", headers=_bearer(user), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _budget(
    client: AsyncClient, user: User, period: str, amount: str, area: str = "all"
) -> dict:
    resp = await client.put(
        f"{BASE}/budgets",
        headers=_bearer(user),
        json={"period": period, "practice_area": area, "amount": amount},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ledger_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "period": "2026-Q1",
        "category": "billing_adjustments",
        "amount": "31400",
        "description": "E-billing write-downs",
        "method_note": "Sum of guideline write-downs taken in the e-billing system",
        "source": "E-billing adjustments report, Q1",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Firms and partners
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_firm_crud_and_flags(client: AsyncClient, user_a: User) -> None:
    headers = _bearer(user_a)
    firm = await _firm(client, user_a, discount_pct="10", rate_increase_pct="6", rate_year=2026)
    assert firm["discount_pct"] == "10"
    assert firm["below_discount_floor"] is False
    assert firm["above_increase_cap"] is True
    assert firm["spend_total"] == "0.00"
    assert firm["partners"] == []

    resp = await client.patch(
        f"{BASE}/firms/{firm['id']}", headers=headers, json={"discount_pct": "8"}
    )
    assert resp.status_code == 200
    assert resp.json()["below_discount_floor"] is True

    resp = await client.get(f"{BASE}/firms", headers=headers)
    assert [f["name"] for f in resp.json()] == ["Hartwell & Crane LLP"]

    resp = await client.delete(f"{BASE}/firms/{firm['id']}", headers=headers)
    assert resp.status_code == 204
    resp = await client.get(f"{BASE}/firms", headers=headers)
    assert resp.json() == []
    resp = await client.delete(f"{BASE}/firms/{firm['id']}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.integration
async def test_firm_validation(client: AsyncClient, user_a: User) -> None:
    headers = _bearer(user_a)
    resp = await client.post(f"{BASE}/firms", headers=headers, json={"name": ""})
    assert resp.status_code == 422
    resp = await client.post(
        f"{BASE}/firms", headers=headers, json={"name": "X", "discount_pct": "120"}
    )
    assert resp.status_code == 422
    resp = await client.post(f"{BASE}/firms", headers=headers, json={"name": "X", "colour": "red"})
    assert resp.status_code == 422
    resp = await client.get(f"{BASE}/firms/not-a-uuid", headers=headers)
    assert resp.status_code == 400


@pytest.mark.integration
async def test_partner_lifecycle(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    headers = _bearer(user_a)
    stakeholder = Stakeholder(
        owner_id=user_a.id, full_name="Elliot Marchetti", stakeholder_type="outside_counsel"
    )
    db_session.add(stakeholder)
    await db_session.flush()

    firm = await _firm(client, user_a)
    resp = await client.post(
        f"{BASE}/firms/{firm['id']}/partners",
        headers=headers,
        json={
            "name": "Elliot Marchetti",
            "stakeholder_id": str(stakeholder.id),
            "practice_area": "corporate",
        },
    )
    assert resp.status_code == 201, resp.text
    partner = resp.json()
    assert partner["status"] == "active"

    # Marking as left stamps left_at with today when not given.
    resp = await client.patch(
        f"{BASE}/partners/{partner['id']}", headers=headers, json={"status": "left_firm"}
    )
    assert resp.status_code == 200
    assert resp.json()["left_at"] == datetime.now(tz=UTC).date().isoformat()

    resp = await client.get(f"{BASE}/firms/{firm['id']}", headers=headers)
    assert resp.json()["partners"][0]["status"] == "left_firm"

    resp = await client.patch(
        f"{BASE}/partners/{partner['id']}",
        headers=headers,
        json={"status": "followed", "resolution_note": "Work moves with Elliot."},
    )
    assert resp.json()["status"] == "followed"

    resp = await client.delete(f"{BASE}/partners/{partner['id']}", headers=headers)
    assert resp.status_code == 204
    resp = await client.get(f"{BASE}/firms/{firm['id']}", headers=headers)
    assert resp.json()["partners"] == []


@pytest.mark.integration
async def test_partner_rejects_other_users_stakeholder(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    foreign = Stakeholder(owner_id=user_b.id, full_name="Not Yours", stakeholder_type="other")
    db_session.add(foreign)
    await db_session.flush()
    firm = await _firm(client, user_a)
    resp = await client.post(
        f"{BASE}/firms/{firm['id']}/partners",
        headers=_bearer(user_a),
        json={"name": "X", "stakeholder_id": str(foreign.id)},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_invoice_totals_period_and_line_replace(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    headers = _bearer(user_a)
    firm = await _firm(client, user_a)
    invoice = await _invoice(
        client,
        user_a,
        firm["id"],
        invoice_date=date(2026, 5, 31),
        lines=[
            _line("Elliot Marchetti", title="partner", hours="2.5", rate="1282.50"),
            _line("Aaron Feldstein", hours="3", rate="801"),
        ],
    )
    assert invoice["period"] == "2026-Q2"
    assert invoice["total"] == "5609.25"  # 3206.25 + 2403.00
    assert invoice["line_count"] == 2
    assert invoice["firm_name"] == "Hartwell & Crane LLP"

    resp = await client.patch(
        f"{BASE}/invoices/{invoice['id']}",
        headers=headers,
        json={"lines": [_line("Chloe Bertrand", hours="1", rate="576")], "status": "paid"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == "576.00"
    assert body["line_count"] == 1
    assert body["status"] == "paid"

    resp = await client.patch(
        f"{BASE}/invoices/{invoice['id']}",
        headers=headers,
        json={"invoice_date": "2026-07-02"},
    )
    assert resp.json()["period"] == "2026-Q3"

    resp = await client.get(f"{BASE}/invoices?period=2026-Q3", headers=headers)
    assert len(resp.json()) == 1
    resp = await client.get(f"{BASE}/invoices?period=2026-Q1", headers=headers)
    assert resp.json() == []
    resp = await client.get(f"{BASE}/invoices?period=Q1", headers=headers)
    assert resp.status_code == 400

    audits = (
        (await db_session.execute(select(AuditLog.action).where(AuditLog.user_id == user_a.id)))
        .scalars()
        .all()
    )
    assert "mgmt_oc_invoice.create" in audits
    assert "mgmt_oc_invoice.update" in audits


@pytest.mark.integration
async def test_invoice_validation(client: AsyncClient, user_a: User) -> None:
    headers = _bearer(user_a)
    firm = await _firm(client, user_a)
    base = {
        "firm_id": firm["id"],
        "invoice_date": "2026-01-31",
        "practice_area": "corporate",
    }
    resp = await client.post(f"{BASE}/invoices", headers=headers, json={**base, "lines": []})
    assert resp.status_code == 422
    resp = await client.post(
        f"{BASE}/invoices",
        headers=headers,
        json={**base, "lines": [_line("A", title="intern")]},
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"{BASE}/invoices",
        headers=headers,
        json={**base, "firm_id": str(uuid.uuid4()), "lines": [_line("A")]},
    )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_staffing_flag_rule(client: AsyncClient, user_a: User) -> None:
    firm = await _firm(client, user_a)
    day = date(2026, 2, 3)

    # Partner + associate on one call: allowed.
    two = await _invoice(
        client,
        user_a,
        firm["id"],
        lines=[_line("Elliot Marchetti", title="partner", day=day), _line("Aaron Feldstein")],
    )
    assert two["staffing_flags"] == []

    # Three people, same day, same task (punctuation/case ignored): flagged.
    three = await _invoice(
        client,
        user_a,
        firm["id"],
        lines=[
            _line("Elliot Marchetti", title="partner", day=day),
            _line("Aaron Feldstein", day=day, task="call with client re: term sheet"),
            _line("Chloe Bertrand", day=day, task="Call with client RE term sheet."),
        ],
    )
    assert len(three["staffing_flags"]) == 1
    flag = three["staffing_flags"][0]
    assert flag["timekeepers"] == ["Aaron Feldstein", "Chloe Bertrand", "Elliot Marchetti"]
    assert flag["amount"] == "2400.00"

    # Three people but on different days or different tasks: not flagged.
    spread = await _invoice(
        client,
        user_a,
        firm["id"],
        lines=[
            _line("Elliot Marchetti", title="partner", day=day),
            _line("Aaron Feldstein", day=day + timedelta(days=1)),
            _line("Chloe Bertrand", day=day, task="Draft disclosure schedules"),
        ],
    )
    assert spread["staffing_flags"] == []

    # The same person billing twice still counts once.
    repeat = await _invoice(
        client,
        user_a,
        firm["id"],
        lines=[
            _line("Aaron Feldstein", day=day),
            _line("Aaron Feldstein", day=day),
            _line("Chloe Bertrand", day=day),
        ],
    )
    assert repeat["staffing_flags"] == []


def test_normalise_task_and_period_for() -> None:
    assert normalise_task("  Call w/ client, RE:  NDA ") == "call w client re nda"
    assert period_for(date(2026, 1, 1)) == "2026-Q1"
    assert period_for(date(2026, 6, 30)) == "2026-Q2"
    assert period_for(date(2026, 12, 31)) == "2026-Q4"


# ---------------------------------------------------------------------------
# Budgets and value ledger
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_budget_upsert_and_delete(client: AsyncClient, user_a: User) -> None:
    headers = _bearer(user_a)
    first = await _budget(client, user_a, "2026-Q1", "905000")
    again = await _budget(client, user_a, "2026-Q1", "950000")
    assert again["id"] == first["id"]
    assert again["amount"] == "950000.00"
    await _budget(client, user_a, "2026-Q1", "200000", area="employment")

    resp = await client.get(f"{BASE}/budgets?year=2026", headers=headers)
    assert [(b["practice_area"], b["amount"]) for b in resp.json()] == [
        ("all", "950000.00"),
        ("employment", "200000.00"),
    ]
    resp = await client.put(
        f"{BASE}/budgets", headers=headers, json={"period": "2026-05", "amount": "1"}
    )
    assert resp.status_code == 422
    resp = await client.delete(f"{BASE}/budgets/{first['id']}", headers=headers)
    assert resp.status_code == 204


@pytest.mark.integration
async def test_value_ledger_requires_receipts(client: AsyncClient, user_a: User) -> None:
    headers = _bearer(user_a)
    for missing in ("method_note", "source"):
        body = _ledger_body()
        del body[missing]
        resp = await client.post(f"{BASE}/value-entries", headers=headers, json=body)
        assert resp.status_code == 422, missing
        resp = await client.post(
            f"{BASE}/value-entries", headers=headers, json=_ledger_body(**{missing: "   "})
        )
        assert resp.status_code == 422, missing

    resp = await client.post(f"{BASE}/value-entries", headers=headers, json=_ledger_body())
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["amount"] == "31400.00"

    resp = await client.patch(
        f"{BASE}/value-entries/{entry['id']}", headers=headers, json={"method_note": None}
    )
    assert resp.status_code in (400, 422)
    resp = await client.patch(
        f"{BASE}/value-entries/{entry['id']}", headers=headers, json={"amount": "32000"}
    )
    assert resp.json()["amount"] == "32000.00"

    resp = await client.get(f"{BASE}/value-entries?year=2026", headers=headers)
    assert len(resp.json()) == 1
    resp = await client.delete(f"{BASE}/value-entries/{entry['id']}", headers=headers)
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_summary_bands_and_breakdowns(client: AsyncClient, user_a: User) -> None:
    hartwell = await _firm(client, user_a, discount_pct="10", rate_increase_pct="6")
    barrow = await _firm(client, user_a, name="Barrow Finch LLP", discount_pct="8")
    await _budget(client, user_a, "2026-Q1", "1000")
    await _budget(client, user_a, "2026-Q2", "1000")
    await _budget(client, user_a, "2026-Q3", "1000")
    # Q1 = 1000 (100%, green); Q2 = 1150 (115%, yellow); Q3 = 1300 (130%, red).
    await _invoice(client, user_a, hartwell["id"], lines=[_line("A", hours="1", rate="1000")])
    await _invoice(
        client,
        user_a,
        barrow["id"],
        invoice_date=date(2026, 5, 1),
        practice_area="litigation",
        lines=[_line("B", hours="1", rate="1150")],
    )
    await _invoice(
        client,
        user_a,
        hartwell["id"],
        invoice_date=date(2026, 8, 1),
        lines=[_line("C", hours="1", rate="1300")],
    )
    resp = await client.post(f"{BASE}/value-entries", headers=_bearer(user_a), json=_ledger_body())
    assert resp.status_code == 201

    resp = await client.get(f"{BASE}/summary?year=2026", headers=_bearer(user_a))
    assert resp.status_code == 200, resp.text
    s = resp.json()
    bands = {q["period"]: (q["pct_of_budget"], q["band"]) for q in s["quarters"]}
    assert bands["2026-Q1"] == ("100.0", "green")
    assert bands["2026-Q2"] == ("115.0", "yellow")
    assert bands["2026-Q3"] == ("130.0", "red")
    assert bands["2026-Q4"] == (None, None)
    assert s["year_actual"] == "3450.00"
    assert s["year_budget"] == "3000.00"
    assert s["by_firm"][0]["label"] == "Hartwell & Crane LLP"
    assert s["by_firm"][0]["amount"] == "2300.00"
    assert {r["key"] for r in s["by_practice_area"]} == {"corporate", "litigation"}
    issues = {(f["firm_name"], f["issue"], f["band"]) for f in s["rate_flags"]}
    assert issues == {
        ("Hartwell & Crane LLP", "increase_above_cap", "yellow"),
        ("Barrow Finch LLP", "discount_below_floor", "red"),
    }
    assert s["value_total"] == "31400.00"
    assert s["max_billers_per_task"] == 2

    # Deleting a firm removes its spend from the totals.
    await client.delete(f"{BASE}/firms/{barrow['id']}", headers=_bearer(user_a))
    s = (await client.get(f"{BASE}/summary?year=2026", headers=_bearer(user_a))).json()
    assert s["year_actual"] == "2300.00"

    resp = await client.get(f"{BASE}/summary?year=26", headers=_bearer(user_a))
    assert resp.status_code == 400


@pytest.mark.integration
async def test_owner_isolation(client: AsyncClient, user_a: User, user_b: User) -> None:
    firm = await _firm(client, user_a)
    invoice = await _invoice(client, user_a, firm["id"])
    resp = await client.post(f"{BASE}/value-entries", headers=_bearer(user_a), json=_ledger_body())
    entry = resp.json()
    budget = await _budget(client, user_a, "2026-Q1", "1000")
    b = _bearer(user_b)

    assert (await client.get(f"{BASE}/firms/{firm['id']}", headers=b)).status_code == 404
    assert (await client.patch(f"{BASE}/firms/{firm['id']}", headers=b, json={})).status_code == 404
    assert (await client.get(f"{BASE}/invoices/{invoice['id']}", headers=b)).status_code == 404
    assert (
        await client.patch(f"{BASE}/value-entries/{entry['id']}", headers=b, json={})
    ).status_code == 404
    assert (await client.delete(f"{BASE}/budgets/{budget['id']}", headers=b)).status_code == 404
    resp = await client.post(
        f"{BASE}/invoices",
        headers=b,
        json={
            "firm_id": firm["id"],
            "invoice_date": "2026-01-31",
            "practice_area": "corporate",
            "lines": [_line("X")],
        },
    )
    assert resp.status_code == 404
    assert (await client.get(f"{BASE}/firms", headers=b)).json() == []
    assert (await client.get(f"{BASE}/invoices", headers=b)).json() == []
    s = (await client.get(f"{BASE}/summary?year=2026", headers=b)).json()
    assert s["year_actual"] == "0.00"


# ---------------------------------------------------------------------------
# Urgent Matters — source 4
# ---------------------------------------------------------------------------


async def _urgent(client: AsyncClient, user: User) -> dict:
    resp = await client.get(URGENT_URL, headers=_bearer(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _oc(items: list[dict]) -> list[str]:
    return [i["title"] for i in items if i["kind"] == "outside_counsel"]


@pytest.mark.integration
async def test_urgent_rate_flags(client: AsyncClient, user_a: User) -> None:
    await _firm(client, user_a, name="OK Firm", discount_pct="10", rate_increase_pct="5")
    await _firm(client, user_a, name="Cheap Firm", discount_pct="9.5")
    await _firm(client, user_a, name="Pricey Firm", rate_increase_pct="5.5", rate_year=2026)
    body = await _urgent(client, user_a)
    assert _oc(body["red"]) == ["Discount below 10%: Cheap Firm"]
    assert _oc(body["yellow"]) == ["Rate increase above 5%: Pricey Firm"]
    red = next(i for i in body["red"] if i["kind"] == "outside_counsel")
    assert red["firm_id"] is not None
    assert red["link"] == "/lq-ai/management/outside-counsel"


@pytest.mark.integration
async def test_urgent_departed_partner_until_resolved(client: AsyncClient, user_a: User) -> None:
    headers = _bearer(user_a)
    firm = await _firm(client, user_a, name="Barrow Finch LLP")
    resp = await client.post(
        f"{BASE}/firms/{firm['id']}/partners", headers=headers, json={"name": "Dana Okafor"}
    )
    partner = resp.json()
    assert _oc((await _urgent(client, user_a))["red"]) == []

    await client.patch(
        f"{BASE}/partners/{partner['id']}", headers=headers, json={"status": "left_firm"}
    )
    assert _oc((await _urgent(client, user_a))["red"]) == [
        "Partner left: Dana Okafor (Barrow Finch LLP)"
    ]

    await client.patch(
        f"{BASE}/partners/{partner['id']}", headers=headers, json={"status": "replaced"}
    )
    assert _oc((await _urgent(client, user_a))["red"]) == []


@pytest.mark.integration
async def test_urgent_quarter_spend_bands(client: AsyncClient, user_a: User) -> None:
    today = datetime.now(tz=UTC).date()
    this_q = period_for(today)
    firm = await _firm(client, user_a)
    await _budget(client, user_a, this_q, "1000")

    # 109% — below the yellow line.
    inv = await _invoice(
        client, user_a, firm["id"], invoice_date=today, lines=[_line("A", rate="1090")]
    )
    body = await _urgent(client, user_a)
    assert _oc(body["yellow"]) == [] and _oc(body["red"]) == []

    # 110% — yellow.
    await client.patch(
        f"{BASE}/invoices/{inv['id']}",
        headers=_bearer(user_a),
        json={"lines": [_line("A", rate="1100")]},
    )
    assert _oc((await _urgent(client, user_a))["yellow"]) == [
        f"Outside counsel spend {this_q}: 110.0% of budget"
    ]

    # 125% — red.
    await client.patch(
        f"{BASE}/invoices/{inv['id']}",
        headers=_bearer(user_a),
        json={"lines": [_line("A", rate="1250")]},
    )
    body = await _urgent(client, user_a)
    assert _oc(body["red"]) == [f"Outside counsel spend {this_q}: 125.0% of budget"]
    assert _oc(body["yellow"]) == []


@pytest.mark.integration
async def test_urgent_outside_counsel_ranks_after_commitment_reds(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    from app.models import StakeholderCommitment

    s = Stakeholder(owner_id=user_a.id, full_name="Margo Chair", stakeholder_type="board_chair")
    db_session.add(s)
    await db_session.flush()
    db_session.add(
        StakeholderCommitment(
            stakeholder_id=s.id,
            direction="we_owe",
            description="Board memo",
            due_date=datetime.now(tz=UTC).date() - timedelta(days=1),
            status="open",
        )
    )
    await db_session.flush()
    await _firm(client, user_a, name="Cheap Firm", discount_pct="5")
    body = await _urgent(client, user_a)
    assert [i["kind"] for i in body["red"]] == ["commitment", "outside_counsel"]


@pytest.mark.integration
async def test_urgent_reserved_slots_keep_outside_counsel_visible(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    """Twelve overdue commitments must not hide the outside-counsel reds."""

    from app.models import StakeholderCommitment, StakeholderInteraction

    today = datetime.now(tz=UTC).date()
    chair = Stakeholder(owner_id=user_a.id, full_name="Margo Chair", stakeholder_type="board_chair")
    db_session.add(chair)
    await db_session.flush()
    for n in range(12):
        db_session.add(
            StakeholderCommitment(
                stakeholder_id=chair.id,
                direction="we_owe",
                description=f"Overdue item {n:02d}",
                due_date=today - timedelta(days=30 + n),
                status="open",
            )
        )
    # Six cadence breaches to crowd the 5-slot yellow band.
    for n in range(6):
        s = Stakeholder(
            owner_id=user_a.id,
            full_name=f"Director {n}",
            stakeholder_type="director",
            cadence_target_days=7,
        )
        db_session.add(s)
        await db_session.flush()
        db_session.add(
            StakeholderInteraction(
                stakeholder_id=s.id,
                occurred_at=datetime.now(tz=UTC) - timedelta(days=40 + n),
                channel="call",
                summary_md="Catch-up",
            )
        )
    await db_session.flush()

    headers = _bearer(user_a)
    await _firm(client, user_a, name="Cheap Firm", discount_pct="5")
    await _firm(client, user_a, name="Pricey Firm", rate_increase_pct="9")
    firm = await _firm(client, user_a, name="Barrow Finch LLP")
    resp = await client.post(
        f"{BASE}/firms/{firm['id']}/partners", headers=headers, json={"name": "Dana Okafor"}
    )
    await client.patch(
        f"{BASE}/partners/{resp.json()['id']}", headers=headers, json={"status": "left_firm"}
    )

    body = await _urgent(client, user_a)
    red_kinds = [i["kind"] for i in body["red"]]
    assert len(body["red"]) == 10
    # Two reserved outside-counsel slots; the eight most-overdue
    # commitments fill the rest, and ranking order is kept.
    assert red_kinds == ["commitment"] * 8 + ["outside_counsel"] * 2
    assert body["red"][0]["title"] == "Deliver: Overdue item 11"

    yellow_kinds = [i["kind"] for i in body["yellow"]]
    assert len(body["yellow"]) == 5
    assert yellow_kinds.count("outside_counsel") == 1
    assert yellow_kinds[-1] == "outside_counsel"
