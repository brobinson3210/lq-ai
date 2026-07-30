"""Integration tests for the Management-tab Documents module.

Covers:

* CRUD happy paths for documents (inline-markdown v1 content model).
* Validation — required title/doc_type/content_md, length caps, unknown
  fields (extra="forbid"), non-UUID path ids (400).
* List shape — ``content_md`` is excluded; the computed
  ``content_chars`` reflects the body length.
* Ordering — newest ``doc_date`` first, NULL dates last, ties broken by
  ``created_at`` desc.
* Filters — ``doc_type`` (exact), ``q`` (case-insensitive substring
  over title OR author OR related_tags, not the body), ``date_from`` /
  ``date_to``, and all of them combined.
* Owner isolation — cross-user reads/writes return 404 everywhere so
  ids do not leak across users (same posture as stakeholders/KPIs).
* Soft-delete semantics — DELETE sets ``deleted_at``; the row vanishes
  from list and detail; second DELETE returns 404; content survives in
  the DB.
* Audit rows for create/update/delete; no-op PATCH skips touch+audit.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.main import app
from app.models import AuditLog, MgmtDocument, User
from app.security import create_access_token, hash_password

DOCS_URL = "/api/v1/management/documents"


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
        email=f"mgmt-doc-{suffix or uuid.uuid4().hex[:8]}@example.com",
        display_name=f"Mgmt Doc User {suffix}".strip(),
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


async def _create_document(
    client: AsyncClient,
    user: User,
    *,
    title: str = "Q3 board pack",
    doc_type: str = "board_pack",
    content_md: str = "# Q3 board pack\n\nRevenue up.",
    **extra: object,
) -> dict:
    body: dict[str, object] = {
        "title": title,
        "doc_type": doc_type,
        "content_md": content_md,
        **extra,
    }
    resp = await client.post(DOCS_URL, headers=_bearer(user), json=body)
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
# Create
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_returns_201_persists_and_audits(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    body = {
        "title": "July board minutes",
        "doc_type": "minutes",
        "content_md": "# Minutes\n\n1. Approved the budget.",
        "doc_date": "2026-07-15",
        "author": "Bill Price",
        "related_tags": "budget, governance",
    }
    resp = await client.post(DOCS_URL, headers=_bearer(user_a), json=body)
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["title"] == "July board minutes"
    assert payload["doc_type"] == "minutes"
    assert payload["doc_date"] == "2026-07-15"
    assert payload["author"] == "Bill Price"
    assert payload["related_tags"] == "budget, governance"
    assert payload["content_md"] == body["content_md"]
    assert payload["content_chars"] == len(body["content_md"])
    assert payload["owner_id"] == str(user_a.id)
    assert payload["deleted_at"] is None

    persisted = (
        await db_session.execute(
            select(MgmtDocument).where(MgmtDocument.id == uuid.UUID(payload["id"]))
        )
    ).scalar_one()
    assert persisted.owner_id == user_a.id
    assert persisted.content_md == body["content_md"]

    audits = await _audit_rows(db_session, user_a, "mgmt_document.create")
    assert len(audits) == 1
    assert audits[0].resource_id == payload["id"]
    assert audits[0].details["doc_type"] == "minutes"


@pytest.mark.integration
async def test_create_accepts_free_text_doc_type(client: AsyncClient, user_a: User) -> None:
    # doc_type is an open vocabulary, NOT an enum — arbitrary labels pass.
    doc = await _create_document(client, user_a, doc_type="pre-read: strategy offsite")
    assert doc["doc_type"] == "pre-read: strategy offsite"


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad_payload",
    [
        {"doc_type": "memo", "content_md": "x"},  # missing title
        {"title": "T", "content_md": "x"},  # missing doc_type
        {"title": "T", "doc_type": "memo"},  # missing content_md
        {"title": "", "doc_type": "memo", "content_md": "x"},
        {"title": "x" * 301, "doc_type": "memo", "content_md": "x"},
        {"title": "T", "doc_type": "", "content_md": "x"},
        {"title": "T", "doc_type": "x" * 61, "content_md": "x"},
        {"title": "T", "doc_type": "memo", "content_md": ""},
        {"title": "T", "doc_type": "memo", "content_md": "x", "author": "y" * 201},
        {"title": "T", "doc_type": "memo", "content_md": "x", "doc_date": "not-a-date"},
        {"title": "T", "doc_type": "memo", "content_md": "x", "unknown_field": 1},
    ],
)
async def test_create_rejects_invalid_payloads(
    client: AsyncClient, user_a: User, bad_payload: dict
) -> None:
    resp = await client.post(DOCS_URL, headers=_bearer(user_a), json=bad_payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.integration
async def test_endpoints_require_bearer(client: AsyncClient) -> None:
    assert (await client.get(DOCS_URL)).status_code == 401
    assert (await client.post(DOCS_URL, json={})).status_code == 401
    assert (await client.get(f"{DOCS_URL}/{uuid.uuid4()}")).status_code == 401


# ---------------------------------------------------------------------------
# List shape, ordering, filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_excludes_content_and_carries_content_chars(
    client: AsyncClient, user_a: User
) -> None:
    body = "# Long pack\n\n" + ("lorem ipsum " * 100)
    await _create_document(client, user_a, content_md=body)

    listed = await client.get(DOCS_URL, headers=_bearer(user_a))
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert len(rows) == 1
    assert "content_md" not in rows[0]
    assert rows[0]["content_chars"] == len(body)
    assert rows[0]["title"] == "Q3 board pack"


@pytest.mark.integration
async def test_list_orders_doc_date_desc_nulls_last_then_created_desc(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    # Insert out of order; two undated docs verify the created_at tiebreak.
    older = await _create_document(client, user_a, title="Older undated")
    await _create_document(client, user_a, title="June memo", doc_date="2026-06-01")
    newer = await _create_document(client, user_a, title="Newer undated")
    await _create_document(client, user_a, title="July memo", doc_date="2026-07-10")

    # Pin distinct created_at values: the DB's now() is transaction-stable
    # in the test fixture, so all four rows share one insert timestamp and
    # the created_at tiebreak would otherwise be untestable.
    for doc, created in (
        (older, "2026-07-01T10:00:00+00:00"),
        (newer, "2026-07-02T10:00:00+00:00"),
    ):
        await db_session.execute(
            update(MgmtDocument)
            .where(MgmtDocument.id == uuid.UUID(doc["id"]))
            .values(created_at=datetime.datetime.fromisoformat(created))
        )
    await db_session.flush()

    listed = await client.get(DOCS_URL, headers=_bearer(user_a))
    assert [d["title"] for d in listed.json()] == [
        "July memo",
        "June memo",
        "Newer undated",
        "Older undated",
    ]


@pytest.mark.integration
async def test_list_filters_doc_type_q_and_date_range(client: AsyncClient, user_a: User) -> None:
    await _create_document(
        client,
        user_a,
        title="Q3 board pack",
        doc_type="board_pack",
        doc_date="2026-07-10",
        author="Bill Price",
        related_tags="q3, revenue",
        content_md="Contains the word zephyr in the body only.",
    )
    await _create_document(
        client,
        user_a,
        title="Audit committee minutes",
        doc_type="minutes",
        doc_date="2026-06-20",
        author="Dana Counsel",
        related_tags="audit, governance",
    )
    await _create_document(
        client,
        user_a,
        title="Strategy memo",
        doc_type="memo",
        doc_date="2026-05-01",
        related_tags="offsite",
    )

    a = _bearer(user_a)

    # doc_type: exact match only.
    by_type = await client.get(DOCS_URL, headers=a, params={"doc_type": "minutes"})
    assert [d["title"] for d in by_type.json()] == ["Audit committee minutes"]
    assert (await client.get(DOCS_URL, headers=a, params={"doc_type": "minute"})).json() == []

    # q: case-insensitive substring over title...
    by_title = await client.get(DOCS_URL, headers=a, params={"q": "BOARD"})
    assert [d["title"] for d in by_title.json()] == ["Q3 board pack"]
    # ...author...
    by_author = await client.get(DOCS_URL, headers=a, params={"q": "dana"})
    assert [d["title"] for d in by_author.json()] == ["Audit committee minutes"]
    # ...and related_tags.
    by_tag = await client.get(DOCS_URL, headers=a, params={"q": "governance"})
    assert [d["title"] for d in by_tag.json()] == ["Audit committee minutes"]
    # NOT the body — content-only terms do not match.
    assert (await client.get(DOCS_URL, headers=a, params={"q": "zephyr"})).json() == []

    # date range: inclusive bounds on doc_date.
    ranged = await client.get(
        DOCS_URL, headers=a, params={"date_from": "2026-06-01", "date_to": "2026-07-10"}
    )
    assert [d["title"] for d in ranged.json()] == ["Q3 board pack", "Audit committee minutes"]
    from_only = await client.get(DOCS_URL, headers=a, params={"date_from": "2026-07-01"})
    assert [d["title"] for d in from_only.json()] == ["Q3 board pack"]
    # Malformed dates: 422 from FastAPI's date parsing.
    assert (
        await client.get(DOCS_URL, headers=a, params={"date_from": "July 1"})
    ).status_code == 422

    # Combined: all three filter axes at once.
    combined = await client.get(
        DOCS_URL,
        headers=a,
        params={"doc_type": "minutes", "q": "audit", "date_from": "2026-06-01"},
    )
    assert [d["title"] for d in combined.json()] == ["Audit committee minutes"]
    mismatch = await client.get(
        DOCS_URL,
        headers=a,
        params={"doc_type": "memo", "q": "audit"},
    )
    assert mismatch.json() == []


@pytest.mark.integration
async def test_list_date_range_excludes_undated_documents(
    client: AsyncClient, user_a: User
) -> None:
    await _create_document(client, user_a, title="Undated note")
    await _create_document(client, user_a, title="Dated note", doc_date="2026-07-01")

    ranged = await client.get(DOCS_URL, headers=_bearer(user_a), params={"date_from": "2026-01-01"})
    assert [d["title"] for d in ranged.json()] == ["Dated note"]


# ---------------------------------------------------------------------------
# Detail, PATCH, non-UUID ids
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_detail_includes_content(client: AsyncClient, user_a: User) -> None:
    created = await _create_document(client, user_a, content_md="# Full body")
    got = await client.get(f"{DOCS_URL}/{created['id']}", headers=_bearer(user_a))
    assert got.status_code == 200, got.text
    payload = got.json()
    assert payload["content_md"] == "# Full body"
    assert payload["content_chars"] == len("# Full body")


@pytest.mark.integration
async def test_patch_updates_fields_and_audits(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    created = await _create_document(client, user_a, doc_date="2026-07-01")

    patched = await client.patch(
        f"{DOCS_URL}/{created['id']}",
        headers=_bearer(user_a),
        json={"content_md": "# Revised", "related_tags": "restated", "doc_date": None},
    )
    assert patched.status_code == 200, patched.text
    payload = patched.json()
    assert payload["content_md"] == "# Revised"
    assert payload["content_chars"] == len("# Revised")
    assert payload["related_tags"] == "restated"
    assert payload["doc_date"] is None  # explicit null clears the date
    assert payload["title"] == created["title"]  # untouched
    assert payload["updated_at"] > created["updated_at"]

    audits = await _audit_rows(db_session, user_a, "mgmt_document.update")
    assert len(audits) == 1
    assert audits[0].details["changed_fields"] == ["content_md", "doc_date", "related_tags"]

    # Unknown fields are rejected on PATCH too (extra="forbid").
    resp = await client.patch(
        f"{DOCS_URL}/{created['id']}",
        headers=_bearer(user_a),
        json={"unknown_field": 1},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.integration
async def test_no_op_patch_skips_touch_and_audit(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    created = await _create_document(client, user_a)

    noop = await client.patch(
        f"{DOCS_URL}/{created['id']}",
        headers=_bearer(user_a),
        json={"title": created["title"], "content_md": created["content_md"]},
    )
    assert noop.status_code == 200, noop.text
    assert noop.json()["updated_at"] == created["updated_at"]
    assert await _audit_rows(db_session, user_a, "mgmt_document.update") == []


@pytest.mark.integration
async def test_non_uuid_id_returns_400_and_unknown_404(client: AsyncClient, user_a: User) -> None:
    a = _bearer(user_a)
    assert (await client.get(f"{DOCS_URL}/not-a-uuid", headers=a)).status_code == 400
    assert (
        await client.patch(f"{DOCS_URL}/not-a-uuid", headers=a, json={"title": "x"})
    ).status_code == 400
    assert (await client.delete(f"{DOCS_URL}/not-a-uuid", headers=a)).status_code == 400
    assert (await client.get(f"{DOCS_URL}/{uuid.uuid4()}", headers=a)).status_code == 404


# ---------------------------------------------------------------------------
# Owner isolation (cross-user 404)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_cross_user_access_returns_404_everywhere(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    theirs = await _create_document(client, user_b, title="B's board pack")

    a = _bearer(user_a)
    did = theirs["id"]
    assert (await client.get(f"{DOCS_URL}/{did}", headers=a)).status_code == 404
    assert (
        await client.patch(f"{DOCS_URL}/{did}", headers=a, json={"title": "hijack"})
    ).status_code == 404
    assert (await client.delete(f"{DOCS_URL}/{did}", headers=a)).status_code == 404

    # B's row untouched by the failed writes.
    persisted = (
        await db_session.execute(select(MgmtDocument).where(MgmtDocument.id == uuid.UUID(did)))
    ).scalar_one()
    assert persisted.title == "B's board pack"
    assert persisted.deleted_at is None

    # The list is owner-scoped — B's rows never appear.
    assert (await client.get(DOCS_URL, headers=a)).json() == []
    listed_b = (await client.get(DOCS_URL, headers=_bearer(user_b))).json()
    assert [d["id"] for d in listed_b] == [did]


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_soft_delete_hides_row_and_is_not_repeatable(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    doc = await _create_document(client, user_a)
    a = _bearer(user_a)

    resp = await client.delete(f"{DOCS_URL}/{doc['id']}", headers=a)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    # Tombstoned, content retained in the DB.
    persisted = (
        await db_session.execute(
            select(MgmtDocument).where(MgmtDocument.id == uuid.UUID(doc["id"]))
        )
    ).scalar_one()
    assert persisted.deleted_at is not None
    assert persisted.content_md == doc["content_md"]

    # Invisible via detail, list, PATCH.
    assert (await client.get(f"{DOCS_URL}/{doc['id']}", headers=a)).status_code == 404
    assert (await client.get(DOCS_URL, headers=a)).json() == []
    assert (
        await client.patch(f"{DOCS_URL}/{doc['id']}", headers=a, json={"title": "revive"})
    ).status_code == 404

    # Second delete: 404; exactly one audit row.
    assert (await client.delete(f"{DOCS_URL}/{doc['id']}", headers=a)).status_code == 404
    audits = await _audit_rows(db_session, user_a, "mgmt_document.delete")
    assert len(audits) == 1
    assert audits[0].resource_id == doc["id"]
