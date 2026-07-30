"""Integration tests for the Management-tab Stakeholders module.

Covers:

* CRUD happy paths for stakeholders + the three sub-resources
  (interactions, commitments, positions).
* Owner isolation — cross-user reads/updates/deletes return 404 so ids
  do not leak across users (same posture as projects/saved-prompts).
* Soft-delete behavior — DELETE sets ``deleted_at``; the row disappears
  from list/detail; a second DELETE returns 404; children survive.
* The ``needs_attention`` list filter (days since last interaction vs.
  the per-stakeholder ``cadence_target_days``).
* The flat ``/stakeholder-commitments`` rollup with status/direction
  filters and stakeholder context on each row.
* Positions history — two rows on the same topic; ``latest=true``
  collapses to the row with the newest ``as_of``.
* Computed-field correctness — ``last_interaction_at``,
  ``days_since_last_interaction``, ``open_commitments_count``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import app
from app.models import AuditLog, Stakeholder, StakeholderCommitment, User
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
        email=f"stakeholder-{suffix or uuid.uuid4().hex[:8]}@example.com",
        display_name=f"Stakeholder User {suffix}".strip(),
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


async def _create_stakeholder(
    client: AsyncClient,
    user: User,
    *,
    full_name: str = "Jane Director",
    stakeholder_type: str = "director",
    **extra: object,
) -> dict:
    body: dict[str, object] = {
        "full_name": full_name,
        "stakeholder_type": stakeholder_type,
        **extra,
    }
    resp = await client.post("/api/v1/stakeholders", headers=_bearer(user), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD happy paths
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_returns_201_persists_and_audits(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    body = {
        "full_name": "Alexandra Chair",
        "organization": "Acme Holdings",
        "role_title": "Board Chair",
        "stakeholder_type": "board_chair",
        "committee_seats": "Audit (chair); Compensation",
        "overall_health": "green",
        "cadence_target_days": 14,
        "interests_md": "- succession planning",
        "communication_preferences_md": "Prefers short memos before calls.",
        "notes_md": "Introduced via CEO.",
    }
    resp = await client.post("/api/v1/stakeholders", headers=_bearer(user_a), json=body)
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["full_name"] == "Alexandra Chair"
    assert payload["stakeholder_type"] == "board_chair"
    assert payload["owner_id"] == str(user_a.id)
    assert payload["cadence_target_days"] == 14
    assert payload["deleted_at"] is None
    # Computed fields on a fresh row.
    assert payload["last_interaction_at"] is None
    assert payload["days_since_last_interaction"] is None
    assert payload["open_commitments_count"] == 0

    new_id = uuid.UUID(payload["id"])
    persisted = (
        await db_session.execute(select(Stakeholder).where(Stakeholder.id == new_id))
    ).scalar_one()
    assert persisted.owner_id == user_a.id
    assert persisted.committee_seats == "Audit (chair); Compensation"

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_a.id,
                    AuditLog.action == "stakeholder.create",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].resource_id == str(new_id)


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad_payload",
    [
        {"full_name": "", "stakeholder_type": "director"},
        {"full_name": "x" * 201, "stakeholder_type": "director"},
        {"full_name": "Jane", "stakeholder_type": "committee_chair"},  # not a type
        {"full_name": "Jane"},  # missing stakeholder_type
        {"full_name": "Jane", "stakeholder_type": "director", "cadence_target_days": 0},
        {"full_name": "Jane", "stakeholder_type": "director", "overall_health": "great"},
        # Old 4-level values were retired by migration 0070.
        {"full_name": "Jane", "stakeholder_type": "director", "overall_health": "strong"},
        {"full_name": "Jane", "stakeholder_type": "director", "overall_health": "at_risk"},
        {"full_name": "Jane", "stakeholder_type": "director", "unknown_field": 1},
    ],
)
async def test_create_rejects_invalid_payloads(
    client: AsyncClient, user_a: User, bad_payload: dict
) -> None:
    resp = await client.post("/api/v1/stakeholders", headers=_bearer(user_a), json=bad_payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.integration
async def test_get_returns_owned_stakeholder(client: AsyncClient, user_a: User) -> None:
    created = await _create_stakeholder(client, user_a)
    resp = await client.get(f"/api/v1/stakeholders/{created['id']}", headers=_bearer(user_a))
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == created["id"]


@pytest.mark.integration
async def test_get_unknown_id_returns_404(client: AsyncClient, user_a: User) -> None:
    resp = await client.get(f"/api/v1/stakeholders/{uuid.uuid4()}", headers=_bearer(user_a))
    assert resp.status_code == 404


@pytest.mark.integration
async def test_get_non_uuid_id_returns_400(client: AsyncClient, user_a: User) -> None:
    resp = await client.get("/api/v1/stakeholders/not-a-uuid", headers=_bearer(user_a))
    assert resp.status_code == 400


@pytest.mark.integration
async def test_list_without_bearer_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/stakeholders")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_patch_updates_partial_fields_and_audits(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    created = await _create_stakeholder(client, user_a, overall_health="green")

    resp = await client.patch(
        f"/api/v1/stakeholders/{created['id']}",
        headers=_bearer(user_a),
        json={"overall_health": "red", "cadence_target_days": 30},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall_health"] == "red"
    assert body["cadence_target_days"] == 30
    assert body["full_name"] == created["full_name"]  # untouched

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_a.id,
                    AuditLog.action == "stakeholder.update",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert set(audits[0].details["changed_fields"]) == {"overall_health", "cadence_target_days"}


@pytest.mark.integration
async def test_patch_can_clear_nullable_fields(client: AsyncClient, user_a: User) -> None:
    created = await _create_stakeholder(client, user_a, overall_health="green")
    resp = await client.patch(
        f"/api/v1/stakeholders/{created['id']}",
        headers=_bearer(user_a),
        json={"overall_health": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["overall_health"] is None


@pytest.mark.integration
async def test_patch_no_changes_skips_audit_row(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    created = await _create_stakeholder(client, user_a)
    resp = await client.patch(
        f"/api/v1/stakeholders/{created['id']}",
        headers=_bearer(user_a),
        json={"full_name": created["full_name"]},
    )
    assert resp.status_code == 200, resp.text

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_a.id,
                    AuditLog.action == "stakeholder.update",
                )
            )
        )
        .scalars()
        .all()
    )
    assert audits == []


# ---------------------------------------------------------------------------
# Owner isolation (cross-user 404)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_cross_user_access_returns_404(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    created = await _create_stakeholder(client, user_b)
    sid = created["id"]

    assert (
        await client.get(f"/api/v1/stakeholders/{sid}", headers=_bearer(user_a))
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/stakeholders/{sid}",
            headers=_bearer(user_a),
            json={"full_name": "hijack"},
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/stakeholders/{sid}", headers=_bearer(user_a))
    ).status_code == 404
    # Sub-resources resolve through the parent scope.
    assert (
        await client.get(f"/api/v1/stakeholders/{sid}/interactions", headers=_bearer(user_a))
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/stakeholders/{sid}/commitments",
            headers=_bearer(user_a),
            json={"direction": "we_owe", "description": "sneaky"},
        )
    ).status_code == 404

    # B's row untouched by the failed PATCH/DELETE.
    untouched = (
        await db_session.execute(select(Stakeholder).where(Stakeholder.id == uuid.UUID(sid)))
    ).scalar_one()
    assert untouched.full_name == created["full_name"]
    assert untouched.deleted_at is None


@pytest.mark.integration
async def test_list_is_owner_scoped(client: AsyncClient, user_a: User, user_b: User) -> None:
    await _create_stakeholder(client, user_a, full_name="Mine")
    await _create_stakeholder(client, user_b, full_name="Theirs")

    resp = await client.get("/api/v1/stakeholders", headers=_bearer(user_a))
    assert resp.status_code == 200, resp.text
    names = [row["full_name"] for row in resp.json()]
    assert names == ["Mine"]


@pytest.mark.integration
async def test_commitment_patch_cross_user_returns_404(
    client: AsyncClient, user_a: User, user_b: User
) -> None:
    created = await _create_stakeholder(client, user_b)
    resp = await client.post(
        f"/api/v1/stakeholders/{created['id']}/commitments",
        headers=_bearer(user_b),
        json={"direction": "we_owe", "description": "send deck"},
    )
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/stakeholder-commitments/{cid}",
        headers=_bearer(user_a),
        json={"status": "done"},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_soft_delete_hides_row_and_is_not_repeatable(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    created = await _create_stakeholder(client, user_a)
    sid = created["id"]

    resp = await client.delete(f"/api/v1/stakeholders/{sid}", headers=_bearer(user_a))
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    # Row still exists with the tombstone set (soft delete, not hard).
    persisted = (
        await db_session.execute(select(Stakeholder).where(Stakeholder.id == uuid.UUID(sid)))
    ).scalar_one()
    assert persisted.deleted_at is not None

    # Invisible via detail, list, and sub-resources.
    assert (
        await client.get(f"/api/v1/stakeholders/{sid}", headers=_bearer(user_a))
    ).status_code == 404
    listed = await client.get("/api/v1/stakeholders", headers=_bearer(user_a))
    assert listed.json() == []
    assert (
        await client.get(f"/api/v1/stakeholders/{sid}/interactions", headers=_bearer(user_a))
    ).status_code == 404

    # Second delete: 404 (same idempotency posture as projects).
    resp = await client.delete(f"/api/v1/stakeholders/{sid}", headers=_bearer(user_a))
    assert resp.status_code == 404

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_a.id,
                    AuditLog.action == "stakeholder.delete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].resource_id == sid


@pytest.mark.integration
async def test_soft_deleted_stakeholder_excluded_from_rollup(
    client: AsyncClient, user_a: User
) -> None:
    created = await _create_stakeholder(client, user_a)
    resp = await client.post(
        f"/api/v1/stakeholders/{created['id']}/commitments",
        headers=_bearer(user_a),
        json={"direction": "we_owe", "description": "pre-delete item"},
    )
    assert resp.status_code == 201, resp.text

    assert (
        await client.delete(f"/api/v1/stakeholders/{created['id']}", headers=_bearer(user_a))
    ).status_code == 204

    rollup = await client.get("/api/v1/stakeholder-commitments", headers=_bearer(user_a))
    assert rollup.status_code == 200, rollup.text
    assert rollup.json() == []


# ---------------------------------------------------------------------------
# Filters: stakeholder_type / space / needs_attention
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_filters_by_type_and_space(client: AsyncClient, user_a: User) -> None:
    await _create_stakeholder(client, user_a, full_name="Chair", stakeholder_type="board_chair")
    await _create_stakeholder(client, user_a, full_name="Dir", stakeholder_type="director")
    await _create_stakeholder(client, user_a, full_name="Investor", stakeholder_type="lender")

    by_type = await client.get(
        "/api/v1/stakeholders",
        headers=_bearer(user_a),
        params={"stakeholder_type": "director"},
    )
    assert by_type.status_code == 200, by_type.text
    assert [r["full_name"] for r in by_type.json()] == ["Dir"]

    board = await client.get(
        "/api/v1/stakeholders", headers=_bearer(user_a), params={"space": "board"}
    )
    assert board.status_code == 200, board.text
    assert {r["full_name"] for r in board.json()} == {"Chair", "Dir"}

    investors = await client.get(
        "/api/v1/stakeholders", headers=_bearer(user_a), params={"space": "investors"}
    )
    assert {r["full_name"] for r in investors.json()} == {"Investor"}

    unknown = await client.get(
        "/api/v1/stakeholders", headers=_bearer(user_a), params={"space": "friends"}
    )
    assert unknown.status_code == 400


@pytest.mark.integration
async def test_needs_attention_filter(client: AsyncClient, user_a: User) -> None:
    """Only rows whose days-since-last-interaction exceeds their cadence."""

    overdue = await _create_stakeholder(client, user_a, full_name="Overdue", cadence_target_days=7)
    current = await _create_stakeholder(client, user_a, full_name="Current", cadence_target_days=30)
    no_cadence = await _create_stakeholder(client, user_a, full_name="NoCadence")

    ten_days_ago = (datetime.now(tz=UTC) - timedelta(days=10)).isoformat()
    for sid in (overdue["id"], current["id"], no_cadence["id"]):
        resp = await client.post(
            f"/api/v1/stakeholders/{sid}/interactions",
            headers=_bearer(user_a),
            json={"occurred_at": ten_days_ago, "channel": "call", "summary_md": "Caught up."},
        )
        assert resp.status_code == 201, resp.text

    resp = await client.get(
        "/api/v1/stakeholders",
        headers=_bearer(user_a),
        params={"needs_attention": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert [r["full_name"] for r in resp.json()] == ["Overdue"]


# ---------------------------------------------------------------------------
# Interactions + computed fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_interactions_crud_and_computed_fields(client: AsyncClient, user_a: User) -> None:
    created = await _create_stakeholder(client, user_a)
    sid = created["id"]

    older = datetime.now(tz=UTC) - timedelta(days=20)
    newer = datetime.now(tz=UTC) - timedelta(days=5)
    for occurred_at, channel in ((older, "email"), (newer, "board_meeting")):
        resp = await client.post(
            f"/api/v1/stakeholders/{sid}/interactions",
            headers=_bearer(user_a),
            json={
                "occurred_at": occurred_at.isoformat(),
                "channel": channel,
                "summary_md": f"Interaction via {channel}.",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["stakeholder_id"] == sid

    listed = await client.get(f"/api/v1/stakeholders/{sid}/interactions", headers=_bearer(user_a))
    assert listed.status_code == 200, listed.text
    channels = [r["channel"] for r in listed.json()]
    assert channels == ["board_meeting", "email"]  # newest first

    detail = await client.get(f"/api/v1/stakeholders/{sid}", headers=_bearer(user_a))
    body = detail.json()
    assert body["last_interaction_at"] is not None
    assert datetime.fromisoformat(body["last_interaction_at"]) == newer
    assert body["days_since_last_interaction"] in (4, 5)  # clock-edge tolerant


@pytest.mark.integration
async def test_interaction_rejects_bad_channel(client: AsyncClient, user_a: User) -> None:
    created = await _create_stakeholder(client, user_a)
    resp = await client.post(
        f"/api/v1/stakeholders/{created['id']}/interactions",
        headers=_bearer(user_a),
        json={
            "occurred_at": datetime.now(tz=UTC).isoformat(),
            "channel": "carrier_pigeon",
            "summary_md": "x",
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Commitments: sub-resource, rollup, flat PATCH, open-count
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_commitments_crud_open_count_and_rollup(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    chair = await _create_stakeholder(
        client, user_a, full_name="Alexandra Chair", stakeholder_type="board_chair"
    )
    lender = await _create_stakeholder(
        client, user_a, full_name="Lena Lender", stakeholder_type="lender"
    )
    other = await _create_stakeholder(client, user_b, full_name="Not Mine")

    async def _add(owner: User, sid: str, direction: str, description: str, **extra: object):
        resp = await client.post(
            f"/api/v1/stakeholders/{sid}/commitments",
            headers=_bearer(owner),
            json={"direction": direction, "description": description, **extra},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    board_deck = await _add(user_a, chair["id"], "we_owe", "Send board deck", due_date="2026-07-31")
    await _add(user_a, chair["id"], "they_owe", "Intro to audit partner")
    await _add(user_a, lender["id"], "we_owe", "Q2 covenant certificate", status="done")
    await _add(user_b, other["id"], "we_owe", "B's item")

    # Sub-resource list + status filter.
    listed = await client.get(
        f"/api/v1/stakeholders/{chair['id']}/commitments", headers=_bearer(user_a)
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 2
    open_only = await client.get(
        f"/api/v1/stakeholders/{lender['id']}/commitments",
        headers=_bearer(user_a),
        params={"status": "open"},
    )
    assert open_only.json() == []

    # Computed open-count: chair has 2 open, lender 0 (its item is done).
    chair_detail = (
        await client.get(f"/api/v1/stakeholders/{chair['id']}", headers=_bearer(user_a))
    ).json()
    assert chair_detail["open_commitments_count"] == 2
    lender_detail = (
        await client.get(f"/api/v1/stakeholders/{lender['id']}", headers=_bearer(user_a))
    ).json()
    assert lender_detail["open_commitments_count"] == 0

    # Rollup: only A's stakeholders; rows carry stakeholder context.
    rollup = await client.get("/api/v1/stakeholder-commitments", headers=_bearer(user_a))
    assert rollup.status_code == 200, rollup.text
    rows = rollup.json()
    assert len(rows) == 3
    assert {r["full_name"] for r in rows} == {"Alexandra Chair", "Lena Lender"}
    assert all("stakeholder_type" in r and "stakeholder_id" in r for r in rows)

    # Rollup filters: what do I owe (open + we_owe)?
    owed = await client.get(
        "/api/v1/stakeholder-commitments",
        headers=_bearer(user_a),
        params={"status": "open", "direction": "we_owe"},
    )
    owed_rows = owed.json()
    assert [r["description"] for r in owed_rows] == ["Send board deck"]
    assert owed_rows[0]["stakeholder_type"] == "board_chair"

    # Flat PATCH: close the board-deck item; verify persistence + audit.
    patched = await client.patch(
        f"/api/v1/stakeholder-commitments/{board_deck['id']}",
        headers=_bearer(user_a),
        json={"status": "done", "description": "Send board deck (sent 7/20)"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "done"

    persisted = (
        await db_session.execute(
            select(StakeholderCommitment).where(
                StakeholderCommitment.id == uuid.UUID(board_deck["id"])
            )
        )
    ).scalar_one()
    assert persisted.status == "done"
    assert persisted.description == "Send board deck (sent 7/20)"

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_a.id,
                    AuditLog.action == "stakeholder.commitment_update",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert set(audits[0].details["changed_fields"]) == {"status", "description"}

    # Open-count reflects the closure.
    chair_detail = (
        await client.get(f"/api/v1/stakeholders/{chair['id']}", headers=_bearer(user_a))
    ).json()
    assert chair_detail["open_commitments_count"] == 1


@pytest.mark.integration
async def test_rollup_rejects_bad_filters(client: AsyncClient, user_a: User) -> None:
    resp = await client.get(
        "/api/v1/stakeholder-commitments",
        headers=_bearer(user_a),
        params={"status": "finished"},
    )
    assert resp.status_code == 400
    resp = await client.get(
        "/api/v1/stakeholder-commitments",
        headers=_bearer(user_a),
        params={"direction": "sideways"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Positions history
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_positions_history_and_latest_wins(client: AsyncClient, user_a: User) -> None:
    created = await _create_stakeholder(client, user_a)
    sid = created["id"]

    for as_of, stance in (("2026-05-01", "skeptical"), ("2026-07-01", "supportive")):
        resp = await client.post(
            f"/api/v1/stakeholders/{sid}/positions",
            headers=_bearer(user_a),
            json={
                "topic": "Series C timing",
                "stance": stance,
                "as_of": as_of,
                "note_md": f"As of {as_of}.",
            },
        )
        assert resp.status_code == 201, resp.text
    resp = await client.post(
        f"/api/v1/stakeholders/{sid}/positions",
        headers=_bearer(user_a),
        json={"topic": "CEO succession", "stance": "neutral", "as_of": "2026-06-15"},
    )
    assert resp.status_code == 201, resp.text

    # Full history: both Series C rows present, newest as_of first per topic.
    full = await client.get(f"/api/v1/stakeholders/{sid}/positions", headers=_bearer(user_a))
    assert full.status_code == 200, full.text
    rows = full.json()
    assert len(rows) == 3
    series_c = [r for r in rows if r["topic"] == "Series C timing"]
    assert [r["as_of"] for r in series_c] == ["2026-07-01", "2026-05-01"]

    # latest=true collapses to one row per topic; the latest as_of wins.
    latest = await client.get(
        f"/api/v1/stakeholders/{sid}/positions",
        headers=_bearer(user_a),
        params={"latest": "true"},
    )
    assert latest.status_code == 200, latest.text
    latest_rows = latest.json()
    assert len(latest_rows) == 2
    by_topic = {r["topic"]: r for r in latest_rows}
    assert by_topic["Series C timing"]["stance"] == "supportive"
    assert by_topic["Series C timing"]["as_of"] == "2026-07-01"
    assert by_topic["CEO succession"]["stance"] == "neutral"


@pytest.mark.integration
async def test_position_rejects_bad_stance(client: AsyncClient, user_a: User) -> None:
    created = await _create_stakeholder(client, user_a)
    resp = await client.post(
        f"/api/v1/stakeholders/{created['id']}/positions",
        headers=_bearer(user_a),
        json={"topic": "t", "stance": "hostile", "as_of": "2026-01-01"},
    )
    assert resp.status_code == 422, resp.text
