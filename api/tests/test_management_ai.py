"""Integration tests for the Management-tab AI-features module.

Covers (no real gateway anywhere — the gateway seam is a stub object,
same pattern as ``tests/playbooks/test_executor.py``):

* Wizard questions endpoint — static shape, auth gate.
* Job-creation validation — subject pairing 422s, unknown/cross-user
  subjects 404, missing/empty answers 422, audit row on create, and
  that the background task is scheduled with the new job id.
* Background run happy paths for all three job types via
  :func:`app.management_ai.runner.run_management_ai_job` with a faked
  gateway — including that the pre-meeting-brief context assembly picks
  the name-matched and tag-matched documents and skips the unrelated
  one.
* Gateway-error path → ``status='error'`` with the message persisted.
* kpi_draft JSON parse: retry-once-then-succeed and
  retry-once-then-error.
* List/read shapes — ``params`` excluded from the list read, included
  in the single read; filters; owner isolation (cross-user 404).
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import management_ai as management_ai_module
from app.db.session import get_db
from app.main import app
from app.management_ai.kpi_questions import QUESTIONS
from app.management_ai.runner import run_management_ai_job
from app.models import (
    AuditLog,
    MgmtAiJob,
    MgmtDocument,
    MgmtKpi,
    MgmtKpiDatapoint,
    MgmtTeamMember,
    Stakeholder,
    StakeholderCommitment,
    StakeholderInteraction,
    StakeholderPosition,
    User,
)
from app.security import create_access_token, hash_password

QUESTIONS_URL = "/api/v1/management/kpi-wizard/questions"
JOBS_URL = "/api/v1/management/ai-jobs"


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
        email=f"mgmt-ai-{suffix or uuid.uuid4().hex[:8]}@example.com",
        display_name=f"Mgmt AI User {suffix}".strip(),
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
# Seed helpers (ORM-direct, mirroring the sibling modules' fixtures)
# ---------------------------------------------------------------------------


async def _make_stakeholder(
    db_session: AsyncSession,
    owner: User,
    *,
    full_name: str = "Avery Chen",
    organization: str | None = "Northwind Capital",
    **extra: Any,
) -> Stakeholder:
    row = Stakeholder(
        owner_id=owner.id,
        full_name=full_name,
        organization=organization,
        stakeholder_type="director",
        **extra,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _make_member(
    db_session: AsyncSession,
    owner: User,
    *,
    name: str = "Jordan Ríos",
    department: str = "legal",
    **extra: Any,
) -> MgmtTeamMember:
    row = MgmtTeamMember(
        owner_id=owner.id,
        name=name,
        role_title="Senior Counsel",
        department=department,
        **extra,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _make_document(
    db_session: AsyncSession,
    owner: User,
    *,
    title: str,
    content_md: str,
    related_tags: str | None = None,
    doc_date: datetime.date | None = None,
) -> MgmtDocument:
    row = MgmtDocument(
        owner_id=owner.id,
        title=title,
        doc_type="memo",
        content_md=content_md,
        related_tags=related_tags,
        doc_date=doc_date,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _make_job(
    db_session: AsyncSession,
    owner: User,
    *,
    job_type: str,
    stakeholder_id: uuid.UUID | None = None,
    team_member_id: uuid.UUID | None = None,
    params: dict[str, Any] | None = None,
) -> MgmtAiJob:
    row = MgmtAiJob(
        owner_id=owner.id,
        job_type=job_type,
        stakeholder_id=stakeholder_id,
        team_member_id=team_member_id,
        status="pending",
        params=params,
    )
    db_session.add(row)
    await db_session.flush()
    return row


_ANSWERS = [
    {"question_id": "a1_success_sentence", "answer": "closed deals twice as fast"},
    {"question_id": "b1_efficiency", "answer": "median NDA turnaround, currently ~9 days"},
    {"question_id": "c1_three_numbers", "answer": "turnaround, outside spend, findings past due"},
]

_VALID_DRAFT = {
    "kpis": [
        {
            "name": "Median NDA turnaround",
            "department": "legal",
            "scope": "department",
            "unit": "days",
            "cadence": "monthly",
            "direction": "lower_is_better",
            "baseline": "9",
            "target": None,
            "rationale_md": "The GC named turnaround as the number the business feels (b1).",
        }
    ],
    "not_measured": [
        {"name": "NDAs reviewed", "reason": "Raw activity count; gameable vanity metric."}
    ],
}


# ---------------------------------------------------------------------------
# Stub gateway (mirrors tests/playbooks/test_executor.py::_StubGateway)
# ---------------------------------------------------------------------------


@dataclass
class _StubMessage:
    content: str


@dataclass
class _StubChoice:
    message: _StubMessage


@dataclass
class _StubResponse:
    choices: list[_StubChoice]


@dataclass
class _StubGateway:
    """Returns a queued sequence of completion strings, one per call."""

    contents: list[str] = field(default_factory=list)
    calls_received: list[Any] = field(default_factory=list)

    async def chat_completion(self, request: Any) -> _StubResponse:
        self.calls_received.append(request)
        content = self.contents.pop(0) if self.contents else ""
        return _StubResponse(choices=[_StubChoice(message=_StubMessage(content=content))])


@dataclass
class _FailingGateway:
    calls_received: list[Any] = field(default_factory=list)

    async def chat_completion(self, request: Any) -> _StubResponse:
        self.calls_received.append(request)
        raise RuntimeError("gateway unreachable")


async def _noop_background(**_kwargs: Any) -> None:
    """Replaces ``_run_in_background`` so endpoint tests don't run the job."""


# ---------------------------------------------------------------------------
# Wizard questions endpoint
# ---------------------------------------------------------------------------


async def test_kpi_wizard_questions_shape(client: AsyncClient, user_a: User) -> None:
    resp = await client.get(QUESTIONS_URL, headers=_bearer(user_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    questions = body["questions"]
    assert len(questions) == len(QUESTIONS) >= 12
    for q in questions:
        assert set(q) == {"id", "section", "prompt", "hint", "optional"}
        assert q["id"] and q["prompt"] and q["hint"]
        assert isinstance(q["optional"], bool)
    # Stable ids, all four sections present, script order preserved.
    ids = [q["id"] for q in questions]
    assert len(set(ids)) == len(ids)
    assert {q["section"] for q in questions} == {"A", "B", "C", "D"}
    assert ids == [q["id"] for q in QUESTIONS]


async def test_kpi_wizard_questions_requires_auth(client: AsyncClient) -> None:
    resp = await client.get(QUESTIONS_URL)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Job creation — validation + audit + scheduling
# ---------------------------------------------------------------------------


async def test_create_job_subject_pairing_422(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    headers = _bearer(user_a)
    stakeholder = await _make_stakeholder(db_session, user_a)
    member = await _make_member(db_session, user_a)

    # pre_meeting_brief: stakeholder required, member forbidden.
    resp = await client.post(JOBS_URL, headers=headers, json={"job_type": "pre_meeting_brief"})
    assert resp.status_code == 422
    resp = await client.post(
        JOBS_URL,
        headers=headers,
        json={
            "job_type": "pre_meeting_brief",
            "stakeholder_id": str(stakeholder.id),
            "team_member_id": str(member.id),
        },
    )
    assert resp.status_code == 422

    # review_prep: member required, stakeholder forbidden.
    resp = await client.post(JOBS_URL, headers=headers, json={"job_type": "review_prep"})
    assert resp.status_code == 422
    resp = await client.post(
        JOBS_URL,
        headers=headers,
        json={
            "job_type": "review_prep",
            "team_member_id": str(member.id),
            "stakeholder_id": str(stakeholder.id),
        },
    )
    assert resp.status_code == 422

    # kpi_draft: answers required non-empty, subjects forbidden.
    resp = await client.post(JOBS_URL, headers=headers, json={"job_type": "kpi_draft"})
    assert resp.status_code == 422
    resp = await client.post(
        JOBS_URL, headers=headers, json={"job_type": "kpi_draft", "answers": []}
    )
    assert resp.status_code == 422
    resp = await client.post(
        JOBS_URL,
        headers=headers,
        json={
            "job_type": "kpi_draft",
            "answers": _ANSWERS,
            "stakeholder_id": str(stakeholder.id),
        },
    )
    assert resp.status_code == 422

    # Unknown job_type is a schema-level 422.
    resp = await client.post(JOBS_URL, headers=headers, json={"job_type": "sonnet_draft"})
    assert resp.status_code == 422


async def test_create_job_unknown_and_cross_user_subject_404(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    headers = _bearer(user_a)
    other_stakeholder = await _make_stakeholder(db_session, user_b)
    other_member = await _make_member(db_session, user_b)

    resp = await client.post(
        JOBS_URL,
        headers=headers,
        json={"job_type": "pre_meeting_brief", "stakeholder_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    resp = await client.post(
        JOBS_URL,
        headers=headers,
        json={"job_type": "pre_meeting_brief", "stakeholder_id": str(other_stakeholder.id)},
    )
    assert resp.status_code == 404
    resp = await client.post(
        JOBS_URL,
        headers=headers,
        json={"job_type": "review_prep", "team_member_id": str(other_member.id)},
    )
    assert resp.status_code == 404
    # No job rows were created by any of the failed attempts.
    rows = (await db_session.execute(select(MgmtAiJob))).scalars().all()
    assert rows == []


async def test_create_job_202_schedules_and_audits(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    stakeholder = await _make_stakeholder(db_session, user_a)
    scheduled: list[dict[str, Any]] = []

    async def _record_background(**kwargs: Any) -> None:
        scheduled.append(kwargs)

    with patch.object(management_ai_module, "_run_in_background", new=_record_background):
        resp = await client.post(
            JOBS_URL,
            headers=_bearer(user_a),
            json={"job_type": "pre_meeting_brief", "stakeholder_id": str(stakeholder.id)},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_type"] == "pre_meeting_brief"
    assert body["status"] == "pending"
    assert body["stakeholder_id"] == str(stakeholder.id)
    assert body["team_member_id"] is None
    assert body["result_md"] is None
    assert body["result_json"] is None
    assert body["error"] is None
    assert body["completed_at"] is None
    assert body["params"] is None  # detail shape includes params

    assert len(scheduled) == 1
    assert scheduled[0]["job_id"] == uuid.UUID(body["id"])

    audits = await _audit_rows(db_session, user_a, "mgmt_ai_job.create")
    assert len(audits) == 1
    assert audits[0].resource_id == body["id"]
    assert audits[0].details["job_type"] == "pre_meeting_brief"


async def test_create_kpi_draft_stores_answers_in_params(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    with patch.object(management_ai_module, "_run_in_background", new=_noop_background):
        resp = await client.post(
            JOBS_URL,
            headers=_bearer(user_a),
            json={"job_type": "kpi_draft", "answers": _ANSWERS},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["params"] == {"answers": _ANSWERS}
    row = await db_session.get(MgmtAiJob, uuid.UUID(body["id"]))
    assert row is not None and row.params == {"answers": _ANSWERS}


# ---------------------------------------------------------------------------
# Background run — pre_meeting_brief
# ---------------------------------------------------------------------------


async def test_run_pre_meeting_brief_happy_path(db_session: AsyncSession, user_a: User) -> None:
    stakeholder = await _make_stakeholder(db_session, user_a)
    db_session.add_all(
        [
            StakeholderPosition(
                stakeholder_id=stakeholder.id,
                topic="ipo readiness",
                stance="skeptical",
                note_md="Wants a cleaner cap table first.",
                as_of=datetime.date(2026, 5, 1),
            ),
            StakeholderPosition(
                stakeholder_id=stakeholder.id,
                topic="ipo readiness",
                stance="supportive",
                note_md="Came around after the audit.",
                as_of=datetime.date(2026, 7, 1),
            ),
            StakeholderInteraction(
                stakeholder_id=stakeholder.id,
                occurred_at=datetime.datetime(2026, 6, 12, 15, 0, tzinfo=datetime.UTC),
                channel="meeting",
                summary_md="Walked through the S-1 timeline.",
            ),
            StakeholderCommitment(
                stakeholder_id=stakeholder.id,
                direction="we_owe",
                description="Send the governance memo",
                due_date=datetime.date(2026, 8, 1),
            ),
        ]
    )
    # One doc matching by name, one by related_tags token, one unrelated.
    await _make_document(
        db_session,
        user_a,
        title="Board pack — Avery Chen briefing",
        content_md="Prep notes for the next board cycle.",
        doc_date=datetime.date(2026, 7, 1),
    )
    await _make_document(
        db_session,
        user_a,
        title="IPO workstream update",
        content_md="Workstream status memo.",
        related_tags="IPO Readiness, governance",
        doc_date=datetime.date(2026, 6, 15),
    )
    await _make_document(
        db_session,
        user_a,
        title="Cafeteria vendor contract notes",
        content_md="Nothing to do with any board member.",
        doc_date=datetime.date(2026, 7, 10),
    )
    job = await _make_job(
        db_session, user_a, job_type="pre_meeting_brief", stakeholder_id=stakeholder.id
    )

    gateway = _StubGateway(contents=["## Who they are\nA fine director. [dossier]"])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "done"
    assert job.result_md == "## Who they are\nA fine director. [dossier]"
    assert job.error is None
    assert job.completed_at is not None

    assert len(gateway.calls_received) == 1
    request = gateway.calls_received[0]
    assert request.model == "smart"
    system = request.messages[0].content
    context = request.messages[1].content
    assert "Avery Chen" in system  # {stakeholder_name} filled
    assert "do not invent" in system.lower() or "not invent" in system.lower()
    # Context assembly picked the right documents...
    assert "Board pack — Avery Chen briefing" in context
    assert "IPO workstream update" in context  # tag token ↔ position topic
    assert "Cafeteria vendor contract notes" not in context
    # ...and the dossier sections.
    assert "Walked through the S-1 timeline." in context
    assert "Send the governance memo" in context
    assert "[position: ipo readiness]" in context
    assert "stance=supportive" in context  # latest first per topic
    assert "Northwind Capital" in context


async def test_run_pre_meeting_brief_truncates_long_documents(
    db_session: AsyncSession, user_a: User
) -> None:
    stakeholder = await _make_stakeholder(db_session, user_a)
    await _make_document(
        db_session,
        user_a,
        title="Avery Chen mega dossier",
        content_md="x" * 20_000,
    )
    job = await _make_job(
        db_session, user_a, job_type="pre_meeting_brief", stakeholder_id=stakeholder.id
    )
    gateway = _StubGateway(contents=["brief"])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    context = gateway.calls_received[0].messages[1].content
    assert "x" * 6_000 in context
    assert "x" * 6_001 not in context
    assert "truncated" in context


# ---------------------------------------------------------------------------
# Background run — review_prep
# ---------------------------------------------------------------------------


async def test_run_review_prep_happy_path(db_session: AsyncSession, user_a: User) -> None:
    member = await _make_member(
        db_session, user_a, strengths_md="Calm under pressure.", department="legal"
    )
    individual = MgmtKpi(
        owner_id=user_a.id,
        name="Contract turnaround",
        department="legal",
        scope="individual",
        team_member_id=member.id,
        unit="days",
        cadence="monthly",
        direction="lower_is_better",
        target=Decimal("5"),
    )
    department = MgmtKpi(
        owner_id=user_a.id,
        name="Outside counsel spend",
        department="legal",
        scope="department",
        unit="USD",
        cadence="quarterly",
        direction="lower_is_better",
    )
    db_session.add_all([individual, department])
    await db_session.flush()
    db_session.add_all(
        [
            MgmtKpiDatapoint(kpi_id=individual.id, period="2026-05", value=Decimal("8")),
            MgmtKpiDatapoint(kpi_id=individual.id, period="2026-06", value=Decimal("6")),
            MgmtKpiDatapoint(kpi_id=department.id, period="2026-Q2", value=Decimal("120000")),
        ]
    )
    await db_session.flush()
    job = await _make_job(db_session, user_a, job_type="review_prep", team_member_id=member.id)

    gateway = _StubGateway(contents=["## KPI performance summary\nTrending well. [kpi: x]"])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "done"
    assert job.result_md is not None and job.result_md.startswith("## KPI performance summary")
    assert job.completed_at is not None

    request = gateway.calls_received[0]
    system = request.messages[0].content
    context = request.messages[1].content
    assert "Jordan Ríos" in system  # {member_name} filled
    assert "[kpi: Contract turnaround]" in context
    assert "2026-05=8, 2026-06=6" in context  # full series, chronological
    assert "[kpi: Outside counsel spend]" in context
    assert "2026-Q2=120000" in context
    assert "Calm under pressure." in context


# ---------------------------------------------------------------------------
# Background run — kpi_draft
# ---------------------------------------------------------------------------


async def test_run_kpi_draft_happy_path(db_session: AsyncSession, user_a: User) -> None:
    job = await _make_job(db_session, user_a, job_type="kpi_draft", params={"answers": _ANSWERS})
    gateway = _StubGateway(contents=[json.dumps(_VALID_DRAFT)])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "done"
    assert job.result_md is None
    assert job.result_json is not None
    assert job.result_json["kpis"][0]["name"] == "Median NDA turnaround"
    assert job.result_json["kpis"][0]["baseline"] == "9"
    assert job.result_json["not_measured"][0]["name"] == "NDAs reviewed"

    # Transcript joins the stored answers with the static question text.
    transcript = gateway.calls_received[0].messages[1].content
    assert "closed deals twice as fast" in transcript
    assert "Finish the sentence" in transcript  # a1's prompt text
    assert "a1_success_sentence" in transcript


async def test_run_kpi_draft_retry_then_success(db_session: AsyncSession, user_a: User) -> None:
    job = await _make_job(db_session, user_a, job_type="kpi_draft", params={"answers": _ANSWERS})
    gateway = _StubGateway(contents=["Sure! Here are some KPIs:", json.dumps(_VALID_DRAFT)])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "done"
    assert job.result_json is not None
    assert len(gateway.calls_received) == 2
    # The retry carried the bad output + the valid-JSON nudge.
    retry_messages = gateway.calls_received[1].messages
    assert retry_messages[-2].role == "assistant"
    assert retry_messages[-2].content == "Sure! Here are some KPIs:"
    assert "valid JSON" in retry_messages[-1].content


async def test_run_kpi_draft_retry_then_error(db_session: AsyncSession, user_a: User) -> None:
    job = await _make_job(db_session, user_a, job_type="kpi_draft", params={"answers": _ANSWERS})
    # Second attempt parses as JSON but fails the shape check (empty kpis).
    gateway = _StubGateway(contents=["not json at all", json.dumps({"kpis": []})])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "error"
    assert job.result_json is None
    assert job.error is not None and "valid KPI-draft JSON" in job.error
    assert job.completed_at is not None
    assert len(gateway.calls_received) == 2


# ---------------------------------------------------------------------------
# Background run — error paths
# ---------------------------------------------------------------------------


async def test_run_gateway_error_persists_error_status(
    db_session: AsyncSession, user_a: User
) -> None:
    stakeholder = await _make_stakeholder(db_session, user_a)
    job = await _make_job(
        db_session, user_a, job_type="pre_meeting_brief", stakeholder_id=stakeholder.id
    )
    gateway = _FailingGateway()
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "error"
    assert job.error is not None and "gateway unreachable" in job.error
    assert job.result_md is None
    assert job.completed_at is not None


async def test_run_subject_deleted_midflight_errors(db_session: AsyncSession, user_a: User) -> None:
    stakeholder = await _make_stakeholder(db_session, user_a)
    job = await _make_job(
        db_session, user_a, job_type="pre_meeting_brief", stakeholder_id=stakeholder.id
    )
    stakeholder.deleted_at = datetime.datetime.now(tz=datetime.UTC)
    await db_session.flush()

    gateway = _StubGateway(contents=["never called"])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "error"
    assert job.error is not None and "no longer exists" in job.error
    assert gateway.calls_received == []


# ---------------------------------------------------------------------------
# List / read shapes + isolation
# ---------------------------------------------------------------------------


async def test_list_excludes_params_detail_includes(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    job = await _make_job(db_session, user_a, job_type="kpi_draft", params={"answers": _ANSWERS})
    headers = _bearer(user_a)

    resp = await client.get(JOBS_URL, headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert "params" not in rows[0]
    assert set(rows[0]) == {
        "id",
        "job_type",
        "status",
        "stakeholder_id",
        "team_member_id",
        "created_at",
        "completed_at",
        "result_md",
        "result_json",
        "error",
    }

    resp = await client.get(f"{JOBS_URL}/{job.id}", headers=headers)
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["params"] == {"answers": _ANSWERS}
    assert detail["id"] == str(job.id)


async def test_list_filters(client: AsyncClient, db_session: AsyncSession, user_a: User) -> None:
    stakeholder = await _make_stakeholder(db_session, user_a)
    member = await _make_member(db_session, user_a)
    brief = await _make_job(
        db_session, user_a, job_type="pre_meeting_brief", stakeholder_id=stakeholder.id
    )
    review = await _make_job(db_session, user_a, job_type="review_prep", team_member_id=member.id)
    await _make_job(db_session, user_a, job_type="kpi_draft", params={"answers": _ANSWERS})
    headers = _bearer(user_a)

    resp = await client.get(JOBS_URL, headers=headers)
    assert len(resp.json()) == 3

    resp = await client.get(JOBS_URL, headers=headers, params={"job_type": "pre_meeting_brief"})
    assert [r["id"] for r in resp.json()] == [str(brief.id)]

    resp = await client.get(
        JOBS_URL, headers=headers, params={"stakeholder_id": str(stakeholder.id)}
    )
    assert [r["id"] for r in resp.json()] == [str(brief.id)]

    resp = await client.get(JOBS_URL, headers=headers, params={"team_member_id": str(member.id)})
    assert [r["id"] for r in resp.json()] == [str(review.id)]

    resp = await client.get(JOBS_URL, headers=headers, params={"job_type": "bogus"})
    assert resp.status_code == 400

    resp = await client.get(JOBS_URL, headers=headers, params={"stakeholder_id": "not-a-uuid"})
    assert resp.status_code == 400


async def test_owner_isolation_404(
    client: AsyncClient, db_session: AsyncSession, user_a: User, user_b: User
) -> None:
    job = await _make_job(db_session, user_a, job_type="kpi_draft", params={"answers": _ANSWERS})

    resp = await client.get(f"{JOBS_URL}/{job.id}", headers=_bearer(user_b))
    assert resp.status_code == 404
    resp = await client.get(JOBS_URL, headers=_bearer(user_b))
    assert resp.json() == []

    resp = await client.get(f"{JOBS_URL}/not-a-uuid", headers=_bearer(user_a))
    assert resp.status_code == 400
    resp = await client.get(f"{JOBS_URL}/{uuid.uuid4()}", headers=_bearer(user_a))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# spend_story (Outside Counsel CFO memo)
# ---------------------------------------------------------------------------


async def test_create_spend_story_rejects_subjects_and_answers(
    client: AsyncClient, db_session: AsyncSession, user_a: User
) -> None:
    headers = _bearer(user_a)
    stakeholder = await _make_stakeholder(db_session, user_a)
    resp = await client.post(
        JOBS_URL,
        headers=headers,
        json={"job_type": "spend_story", "stakeholder_id": str(stakeholder.id)},
    )
    assert resp.status_code == 422
    resp = await client.post(
        JOBS_URL, headers=headers, json={"job_type": "spend_story", "answers": _ANSWERS}
    )
    assert resp.status_code == 422
    with patch.object(management_ai_module, "_run_in_background", _noop_background):
        resp = await client.post(JOBS_URL, headers=headers, json={"job_type": "spend_story"})
    assert resp.status_code == 202, resp.text


async def test_run_spend_story_happy_path(db_session: AsyncSession, user_a: User) -> None:
    from app.models import (
        MgmtOcBudget,
        MgmtOcFirm,
        MgmtOcFirmPartner,
        MgmtOcInvoice,
        MgmtOcInvoiceLine,
        MgmtOcValueEntry,
    )

    year = datetime.datetime.now(tz=datetime.UTC).year
    firm = MgmtOcFirm(
        owner_id=user_a.id,
        name="Hartwell & Crane LLP",
        discount_pct=Decimal("8"),
        rate_increase_pct=Decimal("6"),
        rate_year=year,
    )
    db_session.add(firm)
    await db_session.flush()
    db_session.add(MgmtOcFirmPartner(firm_id=firm.id, name="Elliot Marchetti", status="active"))
    db_session.add(
        MgmtOcBudget(owner_id=user_a.id, period=f"{year}-Q1", practice_area="all", amount=1000)
    )
    invoice = MgmtOcInvoice(
        owner_id=user_a.id,
        firm_id=firm.id,
        invoice_number="HC-101",
        invoice_date=datetime.date(year, 2, 28),
        period=f"{year}-Q1",
        practice_area="corporate",
        status="received",
    )
    db_session.add(invoice)
    await db_session.flush()
    for who in ("Elliot Marchetti", "Aaron Feldstein", "Chloe Bertrand"):
        db_session.add(
            MgmtOcInvoiceLine(
                invoice_id=invoice.id,
                work_date=datetime.date(year, 2, 3),
                timekeeper=who,
                title="associate",
                task="Call re term sheet",
                hours=Decimal("1"),
                rate=Decimal("400"),
                amount=Decimal("400"),
            )
        )
    db_session.add(
        MgmtOcValueEntry(
            owner_id=user_a.id,
            period=f"{year}-Q1",
            category="self_service_savings",
            amount=Decimal("342000"),
            description="FastLane self-service contracts",
            method_note="Blended historical attorney cost per contract type",
            source="CLM export, Q1",
        )
    )
    await db_session.flush()
    job = await _make_job(db_session, user_a, job_type="spend_story")

    gateway = _StubGateway(contents=["## Headline\nSpend is over budget. [spend: Q1]"])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]

    await db_session.refresh(job)
    assert job.status == "done", job.error
    assert job.result_md is not None and job.result_md.startswith("## Headline")
    request = gateway.calls_received[0]
    system = request.messages[0].content
    context = request.messages[1].content
    assert str(year) in system  # {year} filled
    assert f"[spend: {year}-Q1] actual $1200.00; $1000.00; 120.0% of budget; band yellow" in context
    assert "[firm: Hartwell & Crane LLP]" in context
    assert "BELOW DISCOUNT FLOOR" in context and "ABOVE INCREASE CAP" in context
    assert "Elliot Marchetti (active)" in context
    assert "[invoice: HC-101]" in context
    assert "Method: Blended historical attorney cost per contract type." in context
    assert "Source: CLM export, Q1." in context


async def test_run_spend_story_without_data_errors(db_session: AsyncSession, user_a: User) -> None:
    job = await _make_job(db_session, user_a, job_type="spend_story")
    gateway = _StubGateway(contents=["unused"])
    await run_management_ai_job(db_session, job_id=job.id, gateway=gateway)  # type: ignore[arg-type]
    await db_session.refresh(job)
    assert job.status == "error"
    assert "no outside-counsel spend or value data" in (job.error or "")
    assert gateway.calls_received == []
