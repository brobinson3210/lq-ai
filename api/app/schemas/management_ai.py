"""Pydantic schemas for the Management-AI surface (Management tab).

Wire shapes for ``/api/v1/management/ai-jobs*`` and
``/api/v1/management/kpi-wizard/questions``. The ORM model lives in
``app.models.management_ai``; the composer/runner lives in
``app.management_ai.runner``.

Conventions (matching the sibling Management modules):

* Write models use ``extra="forbid"`` so typos in field names fail
  loudly instead of being silently dropped.
* The list shape (:class:`MgmtAiJobListRead`) deliberately **omits**
  ``params`` — wizard answers can be long, and the list endpoint feeds
  the "past briefs" panels. The detail shape (:class:`MgmtAiJobRead`)
  extends it with ``params``.

This module also holds the **KPI-draft validation models**
(:class:`MgmtKpiDraftResult` and children) the runner uses to
shape-check the model's strict-JSON output before persisting it to
``result_json`` — invalid output is retried once, then failed, never
stored unvalidated.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MgmtAiJobType = Literal["pre_meeting_brief", "review_prep", "kpi_draft"]
"""Job types — mirrors ``app.models.management_ai.MGMT_AI_JOB_TYPES``."""

MgmtAiJobStatus = Literal["pending", "running", "done", "error"]
"""Statuses — mirrors ``app.models.management_ai.MGMT_AI_JOB_STATUSES``."""

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
"""Required free-text fields (answers)."""


# ---------------------------------------------------------------------------
# Wizard questions (static, served from kpi_questions.QUESTIONS)
# ---------------------------------------------------------------------------


class MgmtKpiWizardQuestion(BaseModel):
    """One static interview question — data-free, stable ids."""

    id: str
    section: str
    prompt: str
    hint: str
    optional: bool


class MgmtKpiWizardQuestionsRead(BaseModel):
    """``GET /api/v1/management/kpi-wizard/questions`` response."""

    questions: list[MgmtKpiWizardQuestion]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class MgmtKpiWizardAnswer(BaseModel):
    """One collected wizard answer, keyed on the static question id."""

    model_config = ConfigDict(extra="forbid")

    question_id: NonEmptyText
    answer: NonEmptyText


class MgmtAiJobCreate(BaseModel):
    """``POST /api/v1/management/ai-jobs`` body.

    Subject requirements by ``job_type`` (enforced in the handler):

    * ``pre_meeting_brief`` — ``stakeholder_id`` required, no
      ``team_member_id``.
    * ``review_prep`` — ``team_member_id`` required, no
      ``stakeholder_id``.
    * ``kpi_draft`` — no subject; ``answers`` required and non-empty.
    """

    model_config = ConfigDict(extra="forbid")

    job_type: MgmtAiJobType
    stakeholder_id: uuid.UUID | None = None
    team_member_id: uuid.UUID | None = None
    answers: list[MgmtKpiWizardAnswer] | None = None


class MgmtAiJobListRead(BaseModel):
    """List shape — everything except ``params`` (may be large)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: MgmtAiJobType
    status: MgmtAiJobStatus
    stakeholder_id: uuid.UUID | None
    team_member_id: uuid.UUID | None
    created_at: datetime.datetime
    completed_at: datetime.datetime | None
    result_md: str | None
    result_json: dict[str, Any] | None
    error: str | None


class MgmtAiJobRead(MgmtAiJobListRead):
    """Detail shape — the list shape plus the request-side ``params``."""

    params: dict[str, Any] | None


# ---------------------------------------------------------------------------
# KPI-draft output validation (runner-side, never a request body)
# ---------------------------------------------------------------------------


def _coerce_number_string(value: Any) -> Any:
    """Accept a bare JSON number where a string was asked for.

    The prompt demands string numbers, but a model that emits ``12``
    instead of ``"12"`` is still delivering the requested value —
    coerce rather than fail-and-retry over quoting.
    """

    if isinstance(value, (int, float)):
        return format(value, "f") if isinstance(value, float) else str(value)
    return value


class MgmtKpiDraftItem(BaseModel):
    """One drafted KPI. Mirrors the ``MgmtKpiCreate`` field set so a
    confirmed draft maps 1:1 onto ``POST /api/v1/management/kpis``."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    department: Literal["legal", "compliance"]
    scope: Literal["department"]
    unit: NonEmptyText
    cadence: Literal["monthly", "quarterly"]
    direction: Literal["higher_is_better", "lower_is_better"]
    baseline: str | None = None
    target: str | None = None
    rationale_md: NonEmptyText

    _coerce_baseline = field_validator("baseline", "target", mode="before")(_coerce_number_string)


class MgmtKpiDraftNotMeasured(BaseModel):
    """One deliberately-rejected candidate metric, with the why."""

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText
    reason: NonEmptyText


class MgmtKpiDraftResult(BaseModel):
    """The full validated ``kpi_draft`` payload stored in ``result_json``."""

    model_config = ConfigDict(extra="forbid")

    kpis: list[MgmtKpiDraftItem] = Field(min_length=1)
    not_measured: list[MgmtKpiDraftNotMeasured] = Field(default_factory=list)


__all__ = [
    "MgmtAiJobCreate",
    "MgmtAiJobListRead",
    "MgmtAiJobRead",
    "MgmtAiJobStatus",
    "MgmtAiJobType",
    "MgmtKpiDraftItem",
    "MgmtKpiDraftNotMeasured",
    "MgmtKpiDraftResult",
    "MgmtKpiWizardAnswer",
    "MgmtKpiWizardQuestion",
    "MgmtKpiWizardQuestionsRead",
]
