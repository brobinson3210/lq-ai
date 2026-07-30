"""Management-KPIs ORM models — Management tab, KPIs module.

Per-user (owner-scoped) KPI tracking for the legal/compliance function:
the numbers a GC uses to prove the department's value. Three tables
(migration ``0067_management_kpis.py``):

* :class:`MgmtTeamMember` — the roster of people on the caller's legal /
  compliance team. Purely descriptive; ``mgmt_kpis.team_member_id``
  points here for individual-scope KPIs.
* :class:`MgmtKpi` — one KPI definition. ``scope`` is ``department``
  (a function-level number) or ``individual`` (tied to exactly one team
  member — enforced by a table-level CHECK that ``team_member_id`` is
  present iff ``scope='individual'``). ``direction`` says which way is
  good; ``baseline`` / ``target`` bound the attainment computation done
  in the API layer.
* :class:`MgmtKpiDatapoint` — one measured value per KPI per period.
  ``period`` is a text key — ``YYYY-MM`` for monthly KPIs, ``YYYY-Qn``
  for quarterly — validated in the API layer against the parent KPI's
  cadence (the DB stores it opaquely). ``(kpi_id, period)`` is UNIQUE;
  lexicographic ordering of the period string is chronological within
  one cadence, so range filters and series ordering are plain string
  comparisons.

``mgmt_team_members`` and ``mgmt_kpis`` soft-delete via ``deleted_at``
(NULL means active), matching the stakeholders posture. Datapoints
hard-CASCADE off their KPI; individual KPIs hard-CASCADE off their team
member so a future hard-delete stays a one-row operation.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MGMT_DEPARTMENTS: tuple[str, ...] = ("legal", "compliance")
"""Canonical ``department`` values (CHECK-constrained at the DB)."""

MGMT_KPI_SCOPES: tuple[str, ...] = ("department", "individual")
"""Canonical ``mgmt_kpis.scope`` values."""

MGMT_KPI_CADENCES: tuple[str, ...] = ("monthly", "quarterly")
"""Canonical ``mgmt_kpis.cadence`` values — drives the period format."""

MGMT_KPI_DIRECTIONS: tuple[str, ...] = ("higher_is_better", "lower_is_better")
"""Canonical ``mgmt_kpis.direction`` values."""


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """Render ``column IN ('a', 'b', ...)`` for a CHECK constraint."""

    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class MgmtTeamMember(Base):
    """One person on the caller's legal/compliance team roster.

    Owner-scoped: every read/write path filters on ``owner_id`` and
    cross-user access collapses to 404 (same posture as stakeholders).
    ``deleted_at`` is the soft-delete tombstone — NULL means active.
    Soft-deleting a member also tombstones their individual KPIs (done
    in the API layer, same transaction).
    """

    __tablename__ = "mgmt_team_members"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_team_members_name_len",
        ),
        CheckConstraint(
            _in_clause("department", MGMT_DEPARTMENTS),
            name="chk_mgmt_team_members_department",
        ),
        Index("ix_mgmt_team_members_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_team_members_owner_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role_title: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str] = mapped_column(Text, nullable=False)
    seniority: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    development_areas_md: Mapped[str | None] = mapped_column(Text, nullable=True)
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
            f"<MgmtTeamMember id={self.id} owner_id={self.owner_id} "
            f"name={self.name!r} department={self.department!r} "
            f"deleted={self.deleted_at is not None}>"
        )


class MgmtKpi(Base):
    """One KPI definition — "why this number proves legal's value".

    ``scope='department'`` rows have no ``team_member_id``;
    ``scope='individual'`` rows have exactly one — the table-level CHECK
    ``(scope = 'individual') = (team_member_id IS NOT NULL)`` makes the
    pairing structural. ``direction`` orients the attainment math done
    in the API layer (``lower_is_better`` inverts the ratio so >100%
    always means "beating target").
    """

    __tablename__ = "mgmt_kpis"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_kpis_name_len",
        ),
        CheckConstraint(
            _in_clause("department", MGMT_DEPARTMENTS),
            name="chk_mgmt_kpis_department",
        ),
        CheckConstraint(
            _in_clause("scope", MGMT_KPI_SCOPES),
            name="chk_mgmt_kpis_scope",
        ),
        CheckConstraint(
            "(scope = 'individual') = (team_member_id IS NOT NULL)",
            name="chk_mgmt_kpis_scope_team_member",
        ),
        CheckConstraint(
            _in_clause("cadence", MGMT_KPI_CADENCES),
            name="chk_mgmt_kpis_cadence",
        ),
        CheckConstraint(
            _in_clause("direction", MGMT_KPI_DIRECTIONS),
            name="chk_mgmt_kpis_direction",
        ),
        Index("ix_mgmt_kpis_owner_department", "owner_id", "department"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_kpis_owner_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    team_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "mgmt_team_members.id",
            ondelete="CASCADE",
            name="fk_mgmt_kpis_team_member_id",
        ),
        nullable=True,
    )
    # Free-text unit label (e.g. 'days', '%', 'USD', 'count').
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    cadence: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    baseline: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    target: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    rationale_md: Mapped[str | None] = mapped_column(Text, nullable=True)

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
            f"<MgmtKpi id={self.id} owner_id={self.owner_id} name={self.name!r} "
            f"department={self.department!r} scope={self.scope!r} "
            f"deleted={self.deleted_at is not None}>"
        )


class MgmtKpiDatapoint(Base):
    """One measured value for a KPI in one period.

    ``period`` is ``YYYY-MM`` (monthly cadence) or ``YYYY-Qn``
    (quarterly) — the API layer validates the format against the parent
    KPI's cadence; the DB treats it as an opaque UNIQUE-per-KPI key.
    Lexicographic order of the period string is chronological within
    one cadence, which the range filter and series ordering rely on.
    """

    __tablename__ = "mgmt_kpi_datapoints"
    __table_args__ = (
        # The unique constraint's backing index also serves the
        # (kpi_id, period) range scans on the datapoints endpoints — no
        # separate index needed.
        UniqueConstraint("kpi_id", "period", name="uq_mgmt_kpi_datapoints_kpi_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    kpi_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "mgmt_kpis.id",
            ondelete="CASCADE",
            name="fk_mgmt_kpi_datapoints_kpi_id",
        ),
        nullable=False,
    )
    period: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    note_md: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        return f"<MgmtKpiDatapoint id={self.id} kpi_id={self.kpi_id} period={self.period!r}>"
