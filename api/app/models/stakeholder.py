"""Stakeholder ORM models — Management tab, Stakeholders module.

A Stakeholder is a per-user (owner-scoped) record of a person the
executive manages a relationship with — board members, C-suite peers,
investors, lenders, customers, regulators, auditors, outside counsel,
media. Four tables (migration ``0066_stakeholders.py``):

* :class:`Stakeholder` — header row per person. ``stakeholder_type``
  is CHECK-constrained to the canonical set; committee roles are a
  free-text detail on a director (``committee_seats``), not their own
  type. ``overall_health`` is manually set only — the system never
  writes it. ``cadence_target_days`` is the user-configurable contact
  cadence that drives the ``needs_attention`` list filter.
* :class:`StakeholderInteraction` — one row per touchpoint (meeting,
  call, email, ...). ``occurred_at`` feeds the computed
  ``last_interaction_at`` / ``days_since_last_interaction`` fields.
* :class:`StakeholderCommitment` — one row per open-loop item, in
  either direction (``we_owe`` / ``they_owe``), with an
  ``open → done | dropped`` status lifecycle.
* :class:`StakeholderPosition` — per-situation stance rows with
  history: multiple rows per ``topic`` are the record over time; the
  latest ``as_of`` wins for display.

``stakeholders`` soft-deletes via ``deleted_at`` (NULL means active),
matching the files/playbooks posture. Child tables hard-CASCADE off
the stakeholder row so a future hard-delete stays a one-row operation.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STAKEHOLDER_TYPES: tuple[str, ...] = (
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
)
"""Canonical ``stakeholder_type`` values (CHECK-constrained at the DB)."""

STAKEHOLDER_HEALTH_VALUES: tuple[str, ...] = ("green", "yellow", "red")
"""Canonical ``overall_health`` values — a manually-set 3-level traffic
light (migration ``0070`` replaced the old 4-level scale)."""

INTERACTION_CHANNELS: tuple[str, ...] = (
    "meeting",
    "call",
    "email",
    "message",
    "board_meeting",
    "social",
    "other",
)
"""Canonical ``stakeholder_interactions.channel`` values."""

COMMITMENT_DIRECTIONS: tuple[str, ...] = ("we_owe", "they_owe")
"""Canonical ``stakeholder_commitments.direction`` values."""

COMMITMENT_STATUSES: tuple[str, ...] = ("open", "done", "dropped")
"""Canonical ``stakeholder_commitments.status`` values."""

POSITION_STANCES: tuple[str, ...] = (
    "champion",
    "supportive",
    "neutral",
    "skeptical",
    "opposed",
    "unknown",
)
"""Canonical ``stakeholder_positions.stance`` values."""


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """Render ``column IN ('a', 'b', ...)`` for a CHECK constraint."""

    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class Stakeholder(Base):
    """One person the caller manages a relationship with.

    Owner-scoped: every read/write path filters on ``owner_id`` and
    cross-user access collapses to 404 (same posture as projects/files).
    ``deleted_at`` is the soft-delete tombstone — NULL means active.
    """

    __tablename__ = "stakeholders"
    __table_args__ = (
        CheckConstraint(
            "char_length(full_name) > 0 AND char_length(full_name) <= 200",
            name="chk_stakeholders_full_name_len",
        ),
        CheckConstraint(
            _in_clause("stakeholder_type", STAKEHOLDER_TYPES),
            name="chk_stakeholders_type",
        ),
        CheckConstraint(
            f"overall_health IS NULL OR {_in_clause('overall_health', STAKEHOLDER_HEALTH_VALUES)}",
            name="chk_stakeholders_health",
        ),
        CheckConstraint(
            "cadence_target_days IS NULL OR cadence_target_days > 0",
            name="chk_stakeholders_cadence_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_stakeholders_owner_id"),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    organization: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    stakeholder_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Committee roles are a detail on a director, not their own type —
    # free-text so "Audit (chair); Comp" needs no migration to express.
    committee_seats: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Manually set only; the system never derives or overwrites it.
    overall_health: Mapped[str | None] = mapped_column(Text, nullable=True)
    # User-configurable contact cadence in days; drives needs_attention.
    cadence_target_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interests_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    communication_preferences_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_md: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Stakeholder id={self.id} owner_id={self.owner_id} "
            f"full_name={self.full_name!r} type={self.stakeholder_type!r} "
            f"deleted={self.deleted_at is not None}>"
        )


class StakeholderInteraction(Base):
    """One touchpoint with a stakeholder (meeting, call, email, ...).

    ``occurred_at`` is the user-asserted time of the touchpoint (not the
    row-insert time); the ``(stakeholder_id, occurred_at DESC)`` index
    covers the "latest interaction" subquery on the list endpoints.
    """

    __tablename__ = "stakeholder_interactions"
    __table_args__ = (
        CheckConstraint(
            _in_clause("channel", INTERACTION_CHANNELS),
            name="chk_stakeholder_interactions_channel",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    stakeholder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stakeholders.id",
            ondelete="CASCADE",
            name="fk_stakeholder_interactions_stakeholder_id",
        ),
        nullable=False,
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    summary_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        return (
            f"<StakeholderInteraction id={self.id} "
            f"stakeholder_id={self.stakeholder_id} channel={self.channel!r} "
            f"occurred_at={self.occurred_at}>"
        )


class StakeholderCommitment(Base):
    """One open-loop item owed in either direction.

    ``direction`` is ``we_owe`` (the caller owes the stakeholder) or
    ``they_owe``. Status lifecycle is ``open → done | dropped``; the
    ``(stakeholder_id, status)`` index covers the open-count aggregate
    and the rollup endpoint's status filter.
    """

    __tablename__ = "stakeholder_commitments"
    __table_args__ = (
        CheckConstraint(
            _in_clause("direction", COMMITMENT_DIRECTIONS),
            name="chk_stakeholder_commitments_direction",
        ),
        CheckConstraint(
            _in_clause("status", COMMITMENT_STATUSES),
            name="chk_stakeholder_commitments_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    stakeholder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stakeholders.id",
            ondelete="CASCADE",
            name="fk_stakeholder_commitments_stakeholder_id",
        ),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
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

    def __repr__(self) -> str:
        return (
            f"<StakeholderCommitment id={self.id} "
            f"stakeholder_id={self.stakeholder_id} direction={self.direction!r} "
            f"status={self.status!r}>"
        )


class StakeholderPosition(Base):
    """One per-situation stance row; multiple rows per topic = history.

    Display logic takes the latest ``as_of`` per topic (ties broken by
    ``created_at``); older rows are the audit trail of how the stance
    moved. The ``(stakeholder_id, topic, as_of DESC)`` index covers the
    per-topic latest lookup.
    """

    __tablename__ = "stakeholder_positions"
    __table_args__ = (
        CheckConstraint(
            _in_clause("stance", POSITION_STANCES),
            name="chk_stakeholder_positions_stance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    stakeholder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stakeholders.id",
            ondelete="CASCADE",
            name="fk_stakeholder_positions_stakeholder_id",
        ),
        nullable=False,
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    stance: Mapped[str] = mapped_column(Text, nullable=False)
    note_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    as_of: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        return (
            f"<StakeholderPosition id={self.id} "
            f"stakeholder_id={self.stakeholder_id} topic={self.topic!r} "
            f"stance={self.stance!r} as_of={self.as_of}>"
        )
