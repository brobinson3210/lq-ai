"""Management-AI endpoints — Management tab, AI-features module.

Surface (all under ``/api/v1/management``):

* ``GET /management/kpi-wizard/questions`` — the static A2 KPI-interview
  script (data-free; single source of truth for the wizard UI).
* ``POST /management/ai-jobs``             — create a draft job (202) and
  schedule the background run: ``pre_meeting_brief`` (stakeholder),
  ``review_prep`` (team member), or ``kpi_draft`` (wizard answers).
* ``GET /management/ai-jobs``              — newest-first list for the
  "past briefs" panels (``?job_type=&stakeholder_id=&team_member_id=``);
  the list shape excludes ``params``.
* ``GET /management/ai-jobs/{job_id}``     — poll a single job
  (full shape incl. ``params``) until ``status`` reaches ``done`` /
  ``error``.

**Per-user isolation.** Rows are scoped to ``owner_id`` exactly as the
sibling Management modules: cross-user access returns 404, not 403, to
avoid leaking existence, and admins see only their own rows too.
Subject validation resolves through the caller's scope, so another
user's stakeholder/team-member id also collapses to 404.

**Draft-then-confirm.** Job results are drafts the user reviews —
nothing here writes to dossiers, KPI definitions, or documents. The
background machinery mirrors the playbook executor exactly: row at
``pending``, FastAPI ``BackgroundTasks`` wrapper opening its own
session, terminal state always written (see
:mod:`app.management_ai.runner`).

Audit logging: PRD §5.3 — job creation writes an ``audit_log`` row via
:func:`app.audit.audit_action`, riding the same transaction as the
insert (same pattern as stakeholders/KPIs/documents).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.api.dependencies import ActiveUser
from app.audit import audit_action
from app.clients.gateway import GatewayClient, get_gateway_client
from app.db.session import get_db, get_session_factory
from app.errors import NotFound, ValidationError
from app.management_ai.kpi_questions import QUESTIONS
from app.management_ai.runner import MgmtAiRunnerError, run_management_ai_job
from app.models.management_ai import MGMT_AI_JOB_TYPES, MgmtAiJob
from app.models.management_kpi import MgmtTeamMember
from app.models.stakeholder import Stakeholder
from app.schemas.management_ai import (
    MgmtAiJobCreate,
    MgmtAiJobListRead,
    MgmtAiJobRead,
    MgmtKpiWizardQuestion,
    MgmtKpiWizardQuestionsRead,
)

router = APIRouter(prefix="/management", tags=["management-ai"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_id(value: str, *, param: str) -> uuid.UUID:
    """Reject non-UUID path/query ids with the domain 400 (same as projects)."""

    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(
            f"{param} must be a UUID",
            details={param: value},
        ) from exc


def _unprocessable(detail: str) -> HTTPException:
    """Domain 422 with the conventional ``{"detail": ...}`` body."""

    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def _load_visible_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> MgmtAiJob:
    """Load a job scoped to the caller; 404 on miss / cross-user."""

    stmt = select(MgmtAiJob).where(
        MgmtAiJob.id == job_id,
        MgmtAiJob.owner_id == owner_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound(
            f"AI job {job_id} not found.",
            details={"job_id": str(job_id)},
        )
    return row


async def _check_subject(
    db: AsyncSession,
    payload: MgmtAiJobCreate,
    owner_id: uuid.UUID,
) -> None:
    """Enforce the job-type ↔ subject pairing; 422 on shape, 404 on scope.

    Mirrors the DB CHECK (``chk_mgmt_ai_jobs_subject``) so the
    constraint never surfaces as a 500, and resolves the subject
    through the caller's scope so cross-user / deleted / unknown ids
    collapse into 404 without leaking existence.
    """

    if payload.job_type == "pre_meeting_brief":
        if payload.stakeholder_id is None:
            raise _unprocessable("job_type='pre_meeting_brief' requires stakeholder_id.")
        if payload.team_member_id is not None:
            raise _unprocessable("job_type='pre_meeting_brief' forbids team_member_id.")
        stmt = select(Stakeholder.id).where(
            Stakeholder.id == payload.stakeholder_id,
            Stakeholder.owner_id == owner_id,
            Stakeholder.deleted_at.is_(None),
        )
        if (await db.execute(stmt)).scalar_one_or_none() is None:
            raise NotFound(
                f"Stakeholder {payload.stakeholder_id} not found.",
                details={"stakeholder_id": str(payload.stakeholder_id)},
            )
    elif payload.job_type == "review_prep":
        if payload.team_member_id is None:
            raise _unprocessable("job_type='review_prep' requires team_member_id.")
        if payload.stakeholder_id is not None:
            raise _unprocessable("job_type='review_prep' forbids stakeholder_id.")
        stmt = select(MgmtTeamMember.id).where(
            MgmtTeamMember.id == payload.team_member_id,
            MgmtTeamMember.owner_id == owner_id,
            MgmtTeamMember.deleted_at.is_(None),
        )
        if (await db.execute(stmt)).scalar_one_or_none() is None:
            raise NotFound(
                f"Team member {payload.team_member_id} not found.",
                details={"team_member_id": str(payload.team_member_id)},
            )
    else:  # kpi_draft
        if payload.stakeholder_id is not None or payload.team_member_id is not None:
            raise _unprocessable("job_type='kpi_draft' takes no subject id.")
        if not payload.answers:
            raise _unprocessable("job_type='kpi_draft' requires a non-empty answers list.")


# ---------------------------------------------------------------------------
# Wizard questions (static)
# ---------------------------------------------------------------------------


@router.get(
    "/kpi-wizard/questions",
    response_model=MgmtKpiWizardQuestionsRead,
    summary="The static KPI-wizard interview script",
    description=(
        "The A2 KPI-interview question script, served from the server "
        "as the single source of truth (data-free, shareable work "
        "product). The wizard UI renders these in order and submits "
        "the collected answers on ``POST /management/ai-jobs`` with "
        "``job_type='kpi_draft'``."
    ),
)
async def get_kpi_wizard_questions(user: ActiveUser) -> MgmtKpiWizardQuestionsRead:
    del user  # auth only — the script is static and user-independent.
    return MgmtKpiWizardQuestionsRead(
        questions=[MgmtKpiWizardQuestion.model_validate(dict(q)) for q in QUESTIONS]
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@router.post(
    "/ai-jobs",
    response_model=MgmtAiJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a Management-AI draft job and schedule the background run",
    description=(
        "Creates the job row at ``status='pending'`` and returns 202 "
        "immediately; the background run promotes it to ``running`` "
        "and then ``done`` / ``error``. Poll "
        "``GET /management/ai-jobs/{job_id}`` for the result. Subject "
        "pairing: ``pre_meeting_brief`` requires ``stakeholder_id``, "
        "``review_prep`` requires ``team_member_id``, ``kpi_draft`` "
        "requires non-empty ``answers`` (422 on violation; unknown / "
        "cross-user subjects are 404). Results are drafts — confirming "
        "them into dossiers/KPIs goes through the existing CRUD."
    ),
    responses={
        404: {"description": "Subject stakeholder / team member not found"},
        422: {"description": "job_type ↔ subject/answers pairing violated"},
    },
)
async def create_ai_job(
    payload: MgmtAiJobCreate,
    request: Request,
    user: ActiveUser,
    background: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    gateway: Annotated[GatewayClient, Depends(get_gateway_client)],
) -> MgmtAiJobRead:
    await _check_subject(db, payload, user.id)

    params = None
    if payload.job_type == "kpi_draft" and payload.answers is not None:
        params = {"answers": [a.model_dump() for a in payload.answers]}

    job = MgmtAiJob(
        owner_id=user.id,
        job_type=payload.job_type,
        stakeholder_id=payload.stakeholder_id,
        team_member_id=payload.team_member_id,
        status="pending",
        params=params,
    )
    db.add(job)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_ai_job.create",
        resource_type="mgmt_ai_job",
        resource_id=str(job.id),
        request=request,
        details={
            "job_type": job.job_type,
            "stakeholder_id": str(job.stakeholder_id) if job.stakeholder_id else None,
            "team_member_id": str(job.team_member_id) if job.team_member_id else None,
        },
    )
    await db.commit()
    await db.refresh(job)

    # Schedule the run. The runner opens its own DB session (via the
    # wrapper) so the per-request session can close cleanly on 202 —
    # exact same machinery as the playbook executor.
    background.add_task(
        _run_in_background,
        job_id=job.id,
        gateway=gateway,
    )

    log.info(
        "mgmt ai job created",
        extra={
            "event": "mgmt_ai_job_created",
            "user_id": str(user.id),
            "job_id": str(job.id),
            "job_type": job.job_type,
        },
    )

    return MgmtAiJobRead.model_validate(job)


@router.get(
    "/ai-jobs",
    response_model=list[MgmtAiJobListRead],
    summary="List the caller's Management-AI jobs (newest first)",
    description=(
        'Feeds the "past briefs" panels. ``job_type`` / '
        "``stakeholder_id`` / ``team_member_id`` filter the list. The "
        "list shape excludes ``params`` (wizard answers may be large); "
        "fetch a single job for the full shape."
    ),
)
async def list_ai_jobs(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    job_type: Annotated[
        str | None,
        Query(description="Filter to one job type."),
    ] = None,
    stakeholder_id: Annotated[
        str | None,
        Query(description="Filter to one stakeholder's jobs."),
    ] = None,
    team_member_id: Annotated[
        str | None,
        Query(description="Filter to one team member's jobs."),
    ] = None,
) -> list[MgmtAiJobListRead]:
    conditions: list[ColumnElement[bool]] = [MgmtAiJob.owner_id == user.id]
    if job_type is not None:
        if job_type not in MGMT_AI_JOB_TYPES:
            raise ValidationError(
                f"Unknown job_type {job_type!r}.",
                details={"allowed": list(MGMT_AI_JOB_TYPES)},
            )
        conditions.append(MgmtAiJob.job_type == job_type)
    if stakeholder_id is not None:
        sid = _validate_id(stakeholder_id, param="stakeholder_id")
        conditions.append(MgmtAiJob.stakeholder_id == sid)
    if team_member_id is not None:
        mid = _validate_id(team_member_id, param="team_member_id")
        conditions.append(MgmtAiJob.team_member_id == mid)

    stmt = (
        select(MgmtAiJob)
        .where(*conditions)
        .order_by(MgmtAiJob.created_at.desc(), MgmtAiJob.id.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [MgmtAiJobListRead.model_validate(r) for r in rows]


@router.get(
    "/ai-jobs/{job_id}",
    response_model=MgmtAiJobRead,
    summary="Poll a single Management-AI job (owner-only)",
    description=(
        "Returns the row in any state — poll until ``status`` reaches "
        "``done`` (``result_md`` / ``result_json`` populated) or "
        "``error`` (``error`` populated). Includes ``params``."
    ),
    responses={404: {"description": "AI job not found"}},
)
async def get_ai_job(
    job_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtAiJobRead:
    jid = _validate_id(job_id, param="job_id")
    job = await _load_visible_job(db, jid, user.id)
    return MgmtAiJobRead.model_validate(job)


# ---------------------------------------------------------------------------
# Background wrapper
# ---------------------------------------------------------------------------


async def _run_in_background(
    *,
    job_id: uuid.UUID,
    gateway: GatewayClient,
) -> None:
    """Background-task entry point — opens a fresh session for the runner.

    FastAPI's request-scoped session closes when the kick-off handler
    returns 202; the runner needs its own session for the duration of
    the job. Same wrapper shape as the playbook executor's
    ``_run_in_background``.
    """

    factory = get_session_factory()
    async with factory() as session:
        try:
            await run_management_ai_job(session, job_id=job_id, gateway=gateway)
        except MgmtAiRunnerError as exc:
            # Row vanished before the task ran — nothing to persist to;
            # log so the background task doesn't die silently.
            log.warning(
                "management-ai runner refused to start",
                extra={
                    "event": "mgmt_ai_runner_refused",
                    "job_id": str(job_id),
                    "reason": str(exc),
                },
            )


__all__ = [
    "create_ai_job",
    "get_ai_job",
    "get_kpi_wizard_questions",
    "list_ai_jobs",
    "router",
]
