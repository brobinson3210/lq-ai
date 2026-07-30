"""Pydantic schemas for the Stakeholders surface (Management tab).

Wire shapes for ``/api/v1/stakeholders`` and
``/api/v1/stakeholder-commitments``. The ORM models live in
``app.models.stakeholder``; this module is the request/response
surface.

Conventions (matching ``app.schemas.projects``):

* Write models (Create/Update) use ``extra="forbid"`` so typos in
  field names fail loudly instead of being silently dropped.
* Read models use ``from_attributes=True`` so they can be built from
  ORM rows.
* Update models make every field optional; the handlers apply
  ``model_dump(exclude_unset=True)`` so "absent" and "explicit null"
  are distinguishable (clearing ``overall_health`` or
  ``cadence_target_days`` is a legitimate PATCH).

:class:`StakeholderRead` additionally carries three computed fields
the list/detail handlers derive with aggregate subqueries (not stored
columns): ``last_interaction_at``, ``days_since_last_interaction``,
and ``open_commitments_count``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FULL_NAME_MAX_LEN: int = 200
"""Hard cap on ``full_name``. Matches ``chk_stakeholders_full_name_len``."""

StakeholderType = Literal[
    "board_chair",
    "director",
    "ceo",
    "c_suite_peer",
    "investor_sponsor",
    "lender",
    "customer",
    "regulator",
    "auditor",
    "outside_counsel",
    "media",
    "other",
]
"""Canonical stakeholder types. Committee roles are a free-text detail
(``committee_seats``) on a director, not their own type."""

OverallHealth = Literal["green", "yellow", "red"]
"""Manually-set relationship health (3-level traffic light, migration
``0070``); the system never derives it."""

InteractionChannel = Literal[
    "meeting",
    "call",
    "email",
    "message",
    "board_meeting",
    "social",
    "other",
]

CommitmentDirection = Literal["we_owe", "they_owe"]

CommitmentStatus = Literal["open", "done", "dropped"]

PositionStance = Literal[
    "champion",
    "supportive",
    "neutral",
    "skeptical",
    "opposed",
    "unknown",
]

StakeholderFullName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=FULL_NAME_MAX_LEN, strip_whitespace=True),
]
"""1-200 chars, leading/trailing whitespace stripped."""

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
"""Required free-text bodies (summaries, descriptions, topics)."""


# ---------------------------------------------------------------------------
# Stakeholder
# ---------------------------------------------------------------------------


class StakeholderCreate(BaseModel):
    """``POST /api/v1/stakeholders`` body."""

    model_config = ConfigDict(extra="forbid")

    full_name: StakeholderFullName
    organization: str | None = None
    role_title: str | None = None
    stakeholder_type: StakeholderType
    committee_seats: str | None = None
    overall_health: OverallHealth | None = None
    cadence_target_days: int | None = Field(default=None, gt=0)
    interests_md: str | None = None
    communication_preferences_md: str | None = None
    notes_md: str | None = None


class StakeholderUpdate(BaseModel):
    """``PATCH /api/v1/stakeholders/{id}`` body — every field optional.

    The handler applies ``model_dump(exclude_unset=True)`` so nullable
    fields (``overall_health``, ``cadence_target_days``, the ``*_md``
    bodies) can be explicitly cleared with ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: StakeholderFullName | None = None
    organization: str | None = None
    role_title: str | None = None
    stakeholder_type: StakeholderType | None = None
    committee_seats: str | None = None
    overall_health: OverallHealth | None = None
    cadence_target_days: int | None = Field(default=None, gt=0)
    interests_md: str | None = None
    communication_preferences_md: str | None = None
    notes_md: str | None = None


class StakeholderRead(BaseModel):
    """``Stakeholder`` wire shape — row columns plus computed fields.

    ``last_interaction_at`` / ``days_since_last_interaction`` derive
    from the newest ``stakeholder_interactions.occurred_at`` (NULL when
    no interactions are logged); ``open_commitments_count`` counts
    ``status='open'`` commitment rows. All three are computed by the
    handlers with aggregate subqueries in the same SELECT as the
    stakeholder row — never N+1 per-row queries.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    full_name: str
    organization: str | None = None
    role_title: str | None = None
    stakeholder_type: str
    committee_seats: str | None = None
    overall_health: str | None = None
    cadence_target_days: int | None = None
    interests_md: str | None = None
    communication_preferences_md: str | None = None
    notes_md: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None

    # Computed fields (see class docstring).
    last_interaction_at: datetime.datetime | None = None
    days_since_last_interaction: int | None = None
    open_commitments_count: int = 0


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------


class StakeholderInteractionCreate(BaseModel):
    """``POST /api/v1/stakeholders/{id}/interactions`` body."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime.datetime
    channel: InteractionChannel
    summary_md: NonEmptyText


class StakeholderInteractionUpdate(BaseModel):
    """Partial-update shape for an interaction — every field optional."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime.datetime | None = None
    channel: InteractionChannel | None = None
    summary_md: NonEmptyText | None = None


class StakeholderInteractionRead(BaseModel):
    """``StakeholderInteraction`` wire shape."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stakeholder_id: uuid.UUID
    occurred_at: datetime.datetime
    channel: str
    summary_md: str
    created_at: datetime.datetime


# ---------------------------------------------------------------------------
# Commitments
# ---------------------------------------------------------------------------


class StakeholderCommitmentCreate(BaseModel):
    """``POST /api/v1/stakeholders/{id}/commitments`` body.

    ``status`` defaults to ``open`` (matching the DB server default) but
    is accepted on create so an already-resolved item can be logged for
    the record.
    """

    model_config = ConfigDict(extra="forbid")

    direction: CommitmentDirection
    description: NonEmptyText
    due_date: datetime.date | None = None
    status: CommitmentStatus = "open"


class StakeholderCommitmentUpdate(BaseModel):
    """``PATCH /api/v1/stakeholder-commitments/{id}`` body — all optional."""

    model_config = ConfigDict(extra="forbid")

    direction: CommitmentDirection | None = None
    description: NonEmptyText | None = None
    due_date: datetime.date | None = None
    status: CommitmentStatus | None = None


class StakeholderCommitmentRead(BaseModel):
    """``StakeholderCommitment`` wire shape."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stakeholder_id: uuid.UUID
    direction: str
    description: str
    due_date: datetime.date | None = None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class StakeholderCommitmentRollupRead(StakeholderCommitmentRead):
    """Rollup row for ``GET /api/v1/stakeholder-commitments``.

    Extends the commitment shape with enough stakeholder context
    (``full_name``, ``stakeholder_type``) to render "what do I owe the
    board this week" without a second round-trip per row.
    """

    full_name: str
    stakeholder_type: str


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


class StakeholderPositionCreate(BaseModel):
    """``POST /api/v1/stakeholders/{id}/positions`` body.

    Positions are append-only history: recording a stance change means
    POSTing a new row with a later ``as_of``; the latest ``as_of`` per
    topic wins for display.
    """

    model_config = ConfigDict(extra="forbid")

    topic: NonEmptyText
    stance: PositionStance
    note_md: str | None = None
    as_of: datetime.date


class StakeholderPositionRead(BaseModel):
    """``StakeholderPosition`` wire shape."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stakeholder_id: uuid.UUID
    topic: str
    stance: str
    note_md: str | None = None
    as_of: datetime.date
    created_at: datetime.datetime
