"""Management-AI composer/runner — gathers context and runs the draft job.

Public surface: :func:`run_management_ai_job`. The endpoint layer
(``app.api.management_ai``) calls it from a FastAPI ``BackgroundTask``
so the kick-off endpoint can return 202 immediately while the job runs
out-of-band — same machinery as the playbook executor
(:func:`app.playbooks.executor.run_playbook_execution`): fresh session
opened by the endpoint's background wrapper, status flipped to
``running`` on entry, terminal ``done`` / ``error`` written before
exit, exceptions persisted rather than re-raised.

Context is gathered with plain owner-scoped SELECTs over the sibling
Management modules (stakeholders / KPIs / documents) — no embeddings,
no ingestion pipeline. The prompt templates in ``prompts/`` are
data-free, attorney-reviewable assets; this module only fills their
``{placeholders}`` and appends the assembled context as the user
message (system/user split mirrors the playbook nodes).

Gateway calls mirror :func:`app.playbooks.nodes._dispatch_structured_call`:
same client, same default model alias (``smart``), ``anonymize=False``
(the drafts must quote the user's own dossier verbatim),
``lq_ai_purpose`` tagged for routing-log cost calibration, and the
client's :class:`~app.errors.LQAIError` taxonomy caught at the job
boundary and persisted to the row's ``error`` column.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime as dt
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.gateway import GatewayClient
from app.management_ai.kpi_questions import QUESTIONS_BY_ID
from app.models.management_ai import MgmtAiJob
from app.models.management_document import MgmtDocument
from app.models.management_kpi import MgmtKpi, MgmtKpiDatapoint, MgmtTeamMember
from app.models.stakeholder import (
    Stakeholder,
    StakeholderCommitment,
    StakeholderInteraction,
    StakeholderPosition,
)
from app.schemas.gateway import ChatCompletionMessage, ChatCompletionRequest
from app.schemas.management_ai import MgmtKpiDraftResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "smart"
"""Default gateway model alias — matches the playbook executor's
``DEFAULT_JUDGE_MODEL`` (no per-job model selection in v1)."""

BRIEF_MAX_TOKENS = 2500
"""Completion budget for the two brief-shaped jobs (concise by design)."""

KPI_DRAFT_MAX_TOKENS = 3500
"""Completion budget for the strict-JSON KPI catalog."""

MAX_INTERACTIONS = 15
"""Most-recent interactions included in a pre-meeting brief context."""

MAX_POSITION_HISTORY = 3
"""Prior stance rows per topic (beyond the latest) in the brief context."""

MAX_DOCUMENTS = 12
"""Relevant documents included in a pre-meeting brief context."""

DOC_CHAR_BUDGET = 6_000
"""Per-document excerpt cap (characters)."""

TOTAL_CONTEXT_CHAR_BUDGET = 40_000
"""Total assembled-context cap (characters), most-relevant-first."""

DEPT_KPI_DATAPOINTS = 8
"""Latest datapoints per department KPI in a review-prep context."""

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_JSON_RETRY_NUDGE = (
    "Your previous response was not valid JSON matching the required schema. "
    "Return ONLY a single valid JSON object matching the schema exactly — "
    "no markdown fences, no commentary, double quotes, no trailing commas."
)


class MgmtAiRunnerError(Exception):
    """Raised when the runner cannot start (job row missing).

    Distinct from in-run failures, which surface via the ``error``
    column on the ``mgmt_ai_jobs`` row (same split as
    :class:`app.playbooks.executor.PlaybookExecutorError`).
    """


class _ContextError(Exception):
    """In-run failure while assembling context (e.g. subject deleted).

    Caught at the job boundary and persisted as ``status='error'``.
    """


def _load_prompt(name: str) -> str:
    """Read one data-free prompt template from ``prompts/``."""

    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _today() -> str:
    return dt.now(tz=UTC).date().isoformat()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_management_ai_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    gateway: GatewayClient,
    model: str = DEFAULT_MODEL,
) -> None:
    """Run the Management-AI job identified by ``job_id``.

    Moves ``mgmt_ai_jobs.status`` from ``pending`` → ``running`` on
    entry, then to ``done`` (with ``result_md`` / ``result_json``
    populated) or ``error`` (with ``error`` populated) on exit —
    ``completed_at`` is stamped either way, so pollers always reach a
    terminal state.

    Raises :class:`MgmtAiRunnerError` only when the job row itself is
    missing. Every other failure — subject deleted mid-flight, gateway
    errors, malformed model output — is caught and persisted rather
    than re-raised, because the caller is a fire-and-forget FastAPI
    background task.
    """

    job = await db.get(MgmtAiJob, job_id)
    if job is None:
        raise MgmtAiRunnerError(f"MgmtAiJob {job_id} not found")

    job.status = "running"
    job.updated_at = dt.now(tz=UTC)
    await db.commit()

    try:
        if job.job_type == "pre_meeting_brief":
            await _run_pre_meeting_brief(db, job, gateway=gateway, model=model)
        elif job.job_type == "review_prep":
            await _run_review_prep(db, job, gateway=gateway, model=model)
        else:  # kpi_draft — the CHECK constraint admits no other value.
            await _run_kpi_draft(db, job, gateway=gateway, model=model)
    except Exception as exc:
        logger.exception(
            "management-ai job failed",
            extra={
                "event": "mgmt_ai_job_failed",
                "job_id": str(job_id),
                "job_type": job.job_type,
                "error_type": type(exc).__name__,
            },
        )
        _finish(job, status="error", error=f"{type(exc).__name__}: {exc}"[:2000])
        await db.commit()
        return

    await db.commit()
    logger.info(
        "management-ai job finished",
        extra={
            "event": "mgmt_ai_job_finished",
            "job_id": str(job_id),
            "job_type": job.job_type,
            "status": job.status,
        },
    )


def _finish(job: MgmtAiJob, *, status: str, error: str | None = None) -> None:
    """Stamp the terminal state on the row (caller commits)."""

    now = dt.now(tz=UTC)
    job.status = status
    job.error = error
    job.updated_at = now
    job.completed_at = now


# ---------------------------------------------------------------------------
# Gateway dispatch
# ---------------------------------------------------------------------------


async def _dispatch(
    *,
    gateway: GatewayClient,
    model: str,
    messages: list[ChatCompletionMessage],
    max_tokens: int,
) -> str:
    """One non-streaming gateway call; returns the completion text.

    Mirrors the playbook nodes' dispatch: ``temperature`` omitted
    (reasoning models reject it), ``anonymize=False`` (the draft must
    quote the caller's own dossier), ``lq_ai_purpose`` tagged for the
    routing log. Gateway/transport failures propagate as the client's
    typed :class:`~app.errors.LQAIError` subclasses — the job boundary
    in :func:`run_management_ai_job` persists them as ``error``.

    Raises :class:`_ContextError` when the gateway answers with an
    empty completion (nothing usable to persist).
    """

    request = ChatCompletionRequest(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        anonymize=False,
        lq_ai_purpose="management_ai",
    )
    response = await gateway.chat_completion(request)
    choices = getattr(response, "choices", None)
    content = choices[0].message.content if choices else None
    if not content or not content.strip():
        raise _ContextError("the model returned an empty completion")
    return content.strip()


# ---------------------------------------------------------------------------
# pre_meeting_brief
# ---------------------------------------------------------------------------


async def _run_pre_meeting_brief(
    db: AsyncSession,
    job: MgmtAiJob,
    *,
    gateway: GatewayClient,
    model: str,
) -> None:
    stakeholder = await _load_subject_stakeholder(db, job)
    context = await _build_pre_meeting_brief_context(db, stakeholder)

    system = (
        _load_prompt("pre_meeting_brief.md")
        .replace("{stakeholder_name}", stakeholder.full_name)
        .replace("{today}", _today())
    )
    content = await _dispatch(
        gateway=gateway,
        model=model,
        messages=[
            ChatCompletionMessage(role="system", content=system),
            ChatCompletionMessage(role="user", content=context),
        ],
        max_tokens=BRIEF_MAX_TOKENS,
    )
    job.result_md = content
    _finish(job, status="done")


async def _load_subject_stakeholder(db: AsyncSession, job: MgmtAiJob) -> Stakeholder:
    stmt = select(Stakeholder).where(
        Stakeholder.id == job.stakeholder_id,
        Stakeholder.owner_id == job.owner_id,
        Stakeholder.deleted_at.is_(None),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise _ContextError("the stakeholder for this brief no longer exists")
    return row


async def _build_pre_meeting_brief_context(
    db: AsyncSession,
    stakeholder: Stakeholder,
) -> str:
    """Assemble the dossier context block (owner-scoped plain SELECTs).

    Sections: stakeholder record, positions (latest per topic + short
    history), recent interactions, commitments (open first), and up to
    :data:`MAX_DOCUMENTS` relevant documents. Relevance: the document
    mentions the stakeholder's ``full_name`` or ``organization`` in
    title/content (case-insensitive contains), or shares a
    ``related_tags`` token with the stakeholder's position topics.
    Name/organization matches rank above tag matches; within each group
    documents order ``doc_date`` DESC NULLs last. Each document excerpt
    is capped at :data:`DOC_CHAR_BUDGET` chars and the whole block at
    :data:`TOTAL_CONTEXT_CHAR_BUDGET`, most-relevant first; any
    truncation is stated in the block so the model treats absent detail
    as unknown.
    """

    parts: list[str] = [_format_stakeholder(stakeholder)]

    positions = (
        (
            await db.execute(
                select(StakeholderPosition)
                .where(StakeholderPosition.stakeholder_id == stakeholder.id)
                .order_by(
                    StakeholderPosition.topic.asc(),
                    StakeholderPosition.as_of.desc(),
                    StakeholderPosition.created_at.desc(),
                    StakeholderPosition.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    parts.append(_format_positions(positions))

    interactions = (
        (
            await db.execute(
                select(StakeholderInteraction)
                .where(StakeholderInteraction.stakeholder_id == stakeholder.id)
                .order_by(
                    StakeholderInteraction.occurred_at.desc(),
                    StakeholderInteraction.id.desc(),
                )
                .limit(MAX_INTERACTIONS)
            )
        )
        .scalars()
        .all()
    )
    parts.append(_format_interactions(interactions))

    commitments = (
        (
            await db.execute(
                select(StakeholderCommitment)
                .where(StakeholderCommitment.stakeholder_id == stakeholder.id)
                .order_by(
                    # Open first, then done/dropped; soonest due first.
                    (StakeholderCommitment.status != "open").asc(),
                    StakeholderCommitment.due_date.asc().nulls_last(),
                    StakeholderCommitment.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    parts.append(_format_commitments(commitments))

    topics = {p.topic.strip().lower() for p in positions if p.topic and p.topic.strip()}
    documents = await _load_relevant_documents(db, stakeholder, topics)
    parts.append(_format_documents(documents, budget_used=sum(len(p) for p in parts)))

    return "\n\n".join(parts)


def _format_stakeholder(s: Stakeholder) -> str:
    lines = [
        "## Stakeholder record [dossier]",
        f"- Name: {s.full_name}",
        f"- Type: {s.stakeholder_type}",
    ]
    if s.organization:
        lines.append(f"- Organization: {s.organization}")
    if s.role_title:
        lines.append(f"- Role: {s.role_title}")
    if s.committee_seats:
        lines.append(f"- Committee seats: {s.committee_seats}")
    if s.overall_health:
        lines.append(f"- Overall relationship health (manually set): {s.overall_health}")
    if s.cadence_target_days is not None:
        lines.append(f"- Contact cadence target: every {s.cadence_target_days} days")
    if s.interests_md:
        lines.append(f"- Interests:\n{s.interests_md}")
    if s.communication_preferences_md:
        lines.append(f"- Communication preferences:\n{s.communication_preferences_md}")
    if s.notes_md:
        lines.append(f"- Notes:\n{s.notes_md}")
    return "\n".join(lines)


def _format_positions(positions: Sequence[StakeholderPosition]) -> str:
    if not positions:
        return "## Positions by topic\n(none recorded)"
    lines = ["## Positions by topic (latest first per topic; history follows)"]
    seen_per_topic: dict[str, int] = {}
    for p in positions:
        n = seen_per_topic.get(p.topic, 0)
        if n > MAX_POSITION_HISTORY:
            continue
        seen_per_topic[p.topic] = n + 1
        label = "LATEST" if n == 0 else "earlier"
        note = f" — {p.note_md}" if p.note_md else ""
        lines.append(f"- [position: {p.topic}] ({label}, as of {p.as_of}) stance={p.stance}{note}")
    return "\n".join(lines)


def _format_interactions(interactions: Sequence[StakeholderInteraction]) -> str:
    if not interactions:
        return "## Recent interactions\n(none recorded)"
    lines = [f"## Recent interactions (most recent {len(interactions)})"]
    for i in interactions:
        day = i.occurred_at.date().isoformat()
        lines.append(f"- [interaction {day}] via {i.channel}: {i.summary_md}")
    return "\n".join(lines)


def _format_commitments(commitments: Sequence[StakeholderCommitment]) -> str:
    if not commitments:
        return "## Commitments\n(none recorded)"
    lines = ["## Commitments (open first)"]
    for c in commitments:
        who = "we owe them" if c.direction == "we_owe" else "they owe us"
        due = f", due {c.due_date}" if c.due_date else ""
        lines.append(f"- [commitment] ({c.status}, {who}{due}) {c.description}")
    return "\n".join(lines)


async def _load_relevant_documents(
    db: AsyncSession,
    stakeholder: Stakeholder,
    topics: set[str],
) -> list[MgmtDocument]:
    """Pick up to :data:`MAX_DOCUMENTS` relevant docs, most-relevant first.

    Two owner-scoped queries: (1) name/organization contains-matches
    over title + content (ranked first), (2) metadata-only scan of
    ``related_tags`` for a token shared with the position topics
    (ranked second; bodies loaded only for the winners). Both ordered
    ``doc_date`` DESC NULLs last.
    """

    base = [
        MgmtDocument.owner_id == stakeholder.owner_id,
        MgmtDocument.deleted_at.is_(None),
    ]
    order = (
        MgmtDocument.doc_date.desc().nulls_last(),
        MgmtDocument.created_at.desc(),
    )

    # Match on name/organization *tokens*, not the verbatim strings — a
    # dossier's 'Marguerite "Margo" Delclos' must still match documents
    # that say "Margo Delclos". Short tokens (initials, "The") and
    # generic corporate words are skipped so the OR doesn't match the
    # whole corpus.
    generic = {
        "capital",
        "partners",
        "group",
        "holdings",
        "company",
        "corporation",
        "incorporated",
        "systems",
        "bank",
        "advisors",
        "associates",
        "legal",
        "counsel",
        "office",
        "offices",
        "national",
        "american",
        "global",
    }
    needles = [
        token
        for source in (stakeholder.full_name, stakeholder.organization or "")
        for token in source.replace('"', " ").replace(",", " ").split()
        if len(token) >= 4 and token.lower().strip(".") not in generic
    ] or [stakeholder.full_name]
    contains = or_(
        *(
            MgmtDocument.title.icontains(needle, autoescape=True)
            | MgmtDocument.content_md.icontains(needle, autoescape=True)
            for needle in needles
        )
    )

    name_matches = (
        (
            await db.execute(
                select(MgmtDocument).where(*base, contains).order_by(*order).limit(MAX_DOCUMENTS)
            )
        )
        .scalars()
        .all()
    )
    picked: list[MgmtDocument] = list(name_matches)
    picked_ids = {d.id for d in picked}

    if topics and len(picked) < MAX_DOCUMENTS:
        tag_rows = (
            await db.execute(
                select(MgmtDocument.id, MgmtDocument.related_tags).where(
                    *base, MgmtDocument.related_tags.is_not(None)
                )
            )
        ).all()
        tag_ids = [
            doc_id
            for doc_id, related_tags in tag_rows
            if doc_id not in picked_ids
            and any(
                token.strip().lower() in topics
                for token in (related_tags or "").split(",")
                if token.strip()
            )
        ]
        if tag_ids:
            tag_matches = (
                (
                    await db.execute(
                        select(MgmtDocument)
                        .where(MgmtDocument.id.in_(tag_ids))
                        .order_by(*order)
                        .limit(MAX_DOCUMENTS - len(picked))
                    )
                )
                .scalars()
                .all()
            )
            picked.extend(tag_matches)

    return picked


def _format_documents(documents: list[MgmtDocument], *, budget_used: int) -> str:
    if not documents:
        return "## Related documents\n(none matched this stakeholder)"

    lines = ["## Related documents (most relevant first)"]
    remaining = TOTAL_CONTEXT_CHAR_BUDGET - budget_used
    truncated = False
    included = 0
    for d in documents:
        body = d.content_md
        if len(body) > DOC_CHAR_BUDGET:
            body = body[:DOC_CHAR_BUDGET]
            truncated = True
        header = f"### [doc: {d.title}] ({d.doc_type}"
        header += f", dated {d.doc_date})" if d.doc_date else ", undated)"
        block = f"{header}\n{body}"
        if len(block) > remaining:
            truncated = True
            break
        lines.append(block)
        remaining -= len(block)
        included += 1
    if included < len(documents):
        truncated = True
    if truncated:
        lines.append(
            "NOTE: the document context was truncated to fit the budget — "
            "treat absent detail as unknown, not as absent in reality."
        )
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# review_prep
# ---------------------------------------------------------------------------


async def _run_review_prep(
    db: AsyncSession,
    job: MgmtAiJob,
    *,
    gateway: GatewayClient,
    model: str,
) -> None:
    member = await _load_subject_member(db, job)
    context = await _build_review_prep_context(db, member)

    system = (
        _load_prompt("review_prep.md")
        .replace("{member_name}", member.name)
        .replace("{today}", _today())
    )
    content = await _dispatch(
        gateway=gateway,
        model=model,
        messages=[
            ChatCompletionMessage(role="system", content=system),
            ChatCompletionMessage(role="user", content=context),
        ],
        max_tokens=BRIEF_MAX_TOKENS,
    )
    job.result_md = content
    _finish(job, status="done")


async def _load_subject_member(db: AsyncSession, job: MgmtAiJob) -> MgmtTeamMember:
    stmt = select(MgmtTeamMember).where(
        MgmtTeamMember.id == job.team_member_id,
        MgmtTeamMember.owner_id == job.owner_id,
        MgmtTeamMember.deleted_at.is_(None),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise _ContextError("the team member for this review prep no longer exists")
    return row


async def _build_review_prep_context(db: AsyncSession, member: MgmtTeamMember) -> str:
    """Roster record + individual KPI series + department KPI context."""

    parts = [_format_member(member)]

    individual = (
        (
            await db.execute(
                select(MgmtKpi)
                .where(
                    MgmtKpi.owner_id == member.owner_id,
                    MgmtKpi.team_member_id == member.id,
                    MgmtKpi.deleted_at.is_(None),
                )
                .order_by(MgmtKpi.name.asc())
            )
        )
        .scalars()
        .all()
    )
    if individual:
        blocks = ["## Individual KPIs (full series)"]
        for kpi in individual:
            blocks.append(await _format_kpi(db, kpi, limit=None))
        parts.append("\n\n".join(blocks))
    else:
        parts.append("## Individual KPIs\n(none assigned)")

    department = (
        (
            await db.execute(
                select(MgmtKpi)
                .where(
                    MgmtKpi.owner_id == member.owner_id,
                    MgmtKpi.department == member.department,
                    MgmtKpi.scope == "department",
                    MgmtKpi.deleted_at.is_(None),
                )
                .order_by(MgmtKpi.name.asc())
            )
        )
        .scalars()
        .all()
    )
    if department:
        blocks = [
            f"## Department KPIs — {member.department} "
            f"(context; latest {DEPT_KPI_DATAPOINTS} datapoints each)"
        ]
        for kpi in department:
            blocks.append(await _format_kpi(db, kpi, limit=DEPT_KPI_DATAPOINTS))
        parts.append("\n\n".join(blocks))
    else:
        parts.append(f"## Department KPIs — {member.department}\n(none defined)")

    return "\n\n".join(parts)


def _format_member(m: MgmtTeamMember) -> str:
    lines = [
        "## Team-member record [roster note]",
        f"- Name: {m.name}",
        f"- Role: {m.role_title}",
        f"- Department: {m.department}",
    ]
    if m.seniority:
        lines.append(f"- Seniority: {m.seniority}")
    if m.strengths_md:
        lines.append(f"- Recorded strengths:\n{m.strengths_md}")
    if m.development_areas_md:
        lines.append(f"- Recorded development areas:\n{m.development_areas_md}")
    if m.notes_md:
        lines.append(f"- Notes:\n{m.notes_md}")
    return "\n".join(lines)


async def _format_kpi(db: AsyncSession, kpi: MgmtKpi, *, limit: int | None) -> str:
    """One KPI block: definition line + chronological ``period=value`` series."""

    stmt = (
        select(MgmtKpiDatapoint)
        .where(MgmtKpiDatapoint.kpi_id == kpi.id)
        .order_by(MgmtKpiDatapoint.period.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    points = list((await db.execute(stmt)).scalars().all())
    points.reverse()  # chronological for the model

    def _num(value: Any) -> str:
        return "unknown" if value is None else format(value, "f")

    lines = [
        f"### [kpi: {kpi.name}]",
        (
            f"unit={kpi.unit}; cadence={kpi.cadence}; direction={kpi.direction}; "
            f"baseline={_num(kpi.baseline)}; target={_num(kpi.target)}"
        ),
    ]
    if kpi.rationale_md:
        lines.append(f"rationale: {kpi.rationale_md}")
    if points:
        series = ", ".join(f"{p.period}={format(p.value, 'f')}" for p in points)
        lines.append(f"series (chronological): {series}")
    else:
        lines.append("series: (no datapoints recorded)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# kpi_draft
# ---------------------------------------------------------------------------


async def _run_kpi_draft(
    db: AsyncSession,
    job: MgmtAiJob,
    *,
    gateway: GatewayClient,
    model: str,
) -> None:
    del db  # context comes entirely from params; signature kept uniform.
    answers = (job.params or {}).get("answers") or []
    if not answers:
        # The endpoint validates this; belt-and-suspenders for direct runs.
        raise _ContextError("no wizard answers were recorded on this job")

    transcript = _format_answers(answers)
    system = _load_prompt("kpi_interview.md").replace("{today}", _today())
    messages = [
        ChatCompletionMessage(role="system", content=system),
        ChatCompletionMessage(role="user", content=transcript),
    ]

    content = await _dispatch(
        gateway=gateway,
        model=model,
        messages=messages,
        max_tokens=KPI_DRAFT_MAX_TOKENS,
    )
    result = _parse_kpi_draft(content)
    if result is None:
        # One retry with an explicit valid-JSON nudge, then give up.
        retry_messages = [
            *messages,
            ChatCompletionMessage(role="assistant", content=content),
            ChatCompletionMessage(role="user", content=_JSON_RETRY_NUDGE),
        ]
        content = await _dispatch(
            gateway=gateway,
            model=model,
            messages=retry_messages,
            max_tokens=KPI_DRAFT_MAX_TOKENS,
        )
        result = _parse_kpi_draft(content)

    if result is None:
        _finish(
            job,
            status="error",
            error=(
                "The model did not return a valid KPI-draft JSON object "
                "(schema validation failed after one retry). Re-run the "
                "wizard to try again."
            ),
        )
        return

    job.result_json = result.model_dump(mode="json")
    _finish(job, status="done")


def _format_answers(answers: list[dict[str, Any]]) -> str:
    """Join stored answers with the static question script."""

    lines = ["## Interview transcript"]
    for entry in answers:
        qid = str(entry.get("question_id", ""))
        question = QUESTIONS_BY_ID.get(qid)
        if question is not None:
            prompt = question["prompt"]
            section = question["section"]
            lines.append(f"Q ({section} — {qid}): {prompt}")
        else:
            lines.append(f"Q ({qid} — not in the current script):")
        lines.append(f"A: {entry.get('answer', '')}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _parse_kpi_draft(content: str) -> MgmtKpiDraftResult | None:
    """Lenient-parse + shape-check the model's strict-JSON output.

    Trims a leading code fence (same leniency as the playbook nodes'
    ``_parse_json_object``), then ``json.loads`` + Pydantic validation.
    Returns ``None`` on any failure — the caller decides retry/error.
    """

    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rstrip("`").strip()
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    try:
        return MgmtKpiDraftResult.model_validate(decoded)
    except PydanticValidationError:
        return None


__all__ = [
    "DEFAULT_MODEL",
    "MgmtAiRunnerError",
    "run_management_ai_job",
]
