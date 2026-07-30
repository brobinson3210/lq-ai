"""Management-AI ORM model — Management tab, AI-features module.

One table (migration ``0069_management_ai_jobs.py``):

* :class:`MgmtAiJob` — one background AI job per row. ``job_type`` is
  CHECK-constrained to the three supported drafts:

  - ``pre_meeting_brief`` — stakeholder briefing (requires
    ``stakeholder_id``; the table CHECK makes the pairing structural).
  - ``review_prep`` — 1-on-1 performance-review prep (requires
    ``team_member_id``, same structural CHECK).
  - ``kpi_draft`` — KPI-catalog draft from the wizard interview
    (subject-free; ``params`` carries the collected answers).

  ``status`` walks ``pending → running → done | error``. ``result_md``
  carries the markdown brief for the two brief-shaped jobs;
  ``result_json`` carries the validated KPI-draft catalog. ``error``
  is populated on the error path.

**Draft-then-confirm.** Job results are *drafts* the user reviews in
the UI — nothing here writes to stakeholder dossiers, KPI definitions,
or documents. Confirmed rows are created by the user through the
existing CRUD surfaces (same transparency posture as ADR 0016's
governance invariants and the Easy Playbook wizard's Step-3 validate).

``mgmt_ai_jobs`` rows are immutable history (no soft delete, no user
edit path) — the "past briefs" panel lists them newest-first. Subject
FKs use ``ON DELETE SET NULL`` so deleting a stakeholder / team member
keeps the historical job row without a dangling reference.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MGMT_AI_JOB_TYPES: tuple[str, ...] = ("pre_meeting_brief", "review_prep", "kpi_draft")
"""Canonical ``job_type`` values (CHECK-constrained at the DB)."""

MGMT_AI_JOB_STATUSES: tuple[str, ...] = ("pending", "running", "done", "error")
"""Canonical ``status`` values — pending → running → done | error."""


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """Render ``column IN ('a', 'b', ...)`` for a CHECK constraint."""

    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class MgmtAiJob(Base):
    """One background AI-draft job (brief / review prep / KPI draft).

    Owner-scoped: every read/write path filters on ``owner_id`` and
    cross-user access collapses to 404 (same posture as the sibling
    Management modules). The subject pairing is structural: a
    ``pre_meeting_brief`` row always has ``stakeholder_id`` and a
    ``review_prep`` row always has ``team_member_id``
    (``chk_mgmt_ai_jobs_subject``); ``kpi_draft`` rows have neither.
    """

    __tablename__ = "mgmt_ai_jobs"
    __table_args__ = (
        CheckConstraint(
            _in_clause("job_type", MGMT_AI_JOB_TYPES),
            name="chk_mgmt_ai_jobs_job_type",
        ),
        CheckConstraint(
            _in_clause("status", MGMT_AI_JOB_STATUSES),
            name="chk_mgmt_ai_jobs_status",
        ),
        CheckConstraint(
            "(job_type = 'pre_meeting_brief') = (stakeholder_id IS NOT NULL) "
            "AND (job_type = 'review_prep') = (team_member_id IS NOT NULL)",
            name="chk_mgmt_ai_jobs_subject",
        ),
        # Covers the newest-first list endpoint per owner.
        Index("ix_mgmt_ai_jobs_owner_created", "owner_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_ai_jobs_owner_id"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Subject FKs — SET NULL so deleting the subject keeps the job row
    # as history without a dangling reference.
    stakeholder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stakeholders.id",
            ondelete="SET NULL",
            name="fk_mgmt_ai_jobs_stakeholder_id",
        ),
        nullable=True,
    )
    team_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "mgmt_team_members.id",
            ondelete="SET NULL",
            name="fk_mgmt_ai_jobs_team_member_id",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    # Wizard answers etc. — request-side inputs, kept for transparency.
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # The brief (markdown) for pre_meeting_brief / review_prep jobs.
    result_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Validated KPI-draft catalog for kpi_draft jobs.
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<MgmtAiJob id={self.id} owner_id={self.owner_id} "
            f"job_type={self.job_type!r} status={self.status!r}>"
        )
