"""Pydantic schemas for the Management-KPIs surface (Management tab).

Wire shapes for ``/api/v1/management/*``. The ORM models live in
``app.models.management_kpi``; this module is the request/response
surface.

Conventions (matching ``app.schemas.stakeholders``):

* Write models (Create/Update) use ``extra="forbid"`` so typos in field
  names fail loudly instead of being silently dropped.
* Read models use ``from_attributes=True`` where they map 1:1 to ORM
  rows; the KPI read shape is built by hand in the handlers because it
  carries computed columns.
* Update models make every field optional; the handlers apply
  ``model_dump(exclude_unset=True)`` so "absent" and "explicit null"
  are distinguishable.

**Numeric fields serialize as JSON strings** (CLAUDE.md: Decimal cost
fields are strings on the wire). Write models accept ``Decimal`` (JSON
number or string); read models carry ``str`` — the handlers stringify
the DB ``Numeric`` values explicitly, so the wire type is unambiguous.

:class:`MgmtKpiRead` additionally carries five computed fields the
handlers derive with correlated scalar subqueries (not stored columns):
``latest_period``, ``latest_value``, ``previous_value``,
``datapoint_count``, and ``attainment_pct`` (latest vs. target, with the
ratio inverted for ``lower_is_better`` so >100% always means "beating
target"). :class:`MgmtTeamMemberRead` carries ``kpi_count`` — the
number of active KPIs assigned to that member.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

NAME_MAX_LEN: int = 200
"""Hard cap on ``name`` for both team members and KPIs. Matches the
``chk_mgmt_*_name_len`` CHECK constraints."""

Department = Literal["legal", "compliance"]
"""Canonical department values (team members and KPIs)."""

KpiScope = Literal["department", "individual"]
"""``individual`` KPIs are tied to exactly one team member."""

KpiCadence = Literal["monthly", "quarterly"]
"""Drives the datapoint period format: YYYY-MM vs. YYYY-Qn."""

KpiDirection = Literal["higher_is_better", "lower_is_better"]
"""Which way is good — orients the attainment computation."""

MgmtName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=NAME_MAX_LEN, strip_whitespace=True),
]
"""1-200 chars, leading/trailing whitespace stripped."""

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
"""Required free-text fields (role_title, unit, period)."""


# ---------------------------------------------------------------------------
# Team members
# ---------------------------------------------------------------------------


class MgmtTeamMemberCreate(BaseModel):
    """``POST /api/v1/management/team-members`` body."""

    model_config = ConfigDict(extra="forbid")

    name: MgmtName
    role_title: NonEmptyText
    department: Department
    seniority: str | None = None
    strengths_md: str | None = None
    development_areas_md: str | None = None
    notes_md: str | None = None


class MgmtTeamMemberUpdate(BaseModel):
    """``PATCH /api/v1/management/team-members/{id}`` body — all optional.

    The handler applies ``model_dump(exclude_unset=True)`` so nullable
    fields (``seniority``, the ``*_md`` bodies) can be explicitly
    cleared with ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    name: MgmtName | None = None
    role_title: NonEmptyText | None = None
    department: Department | None = None
    seniority: str | None = None
    strengths_md: str | None = None
    development_areas_md: str | None = None
    notes_md: str | None = None


class MgmtTeamMemberRead(BaseModel):
    """``MgmtTeamMember`` wire shape — row columns plus ``kpi_count``.

    ``kpi_count`` is the number of active (non-deleted) KPIs assigned to
    this member, derived with a correlated scalar subquery in the same
    SELECT as the member row(s) — never N+1.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    role_title: str
    department: str
    seniority: str | None = None
    strengths_md: str | None = None
    development_areas_md: str | None = None
    notes_md: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None

    # Computed (see class docstring).
    kpi_count: int = 0


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


class MgmtKpiCreate(BaseModel):
    """``POST /api/v1/management/kpis`` body.

    ``scope='individual'`` requires ``team_member_id`` (and vice versa) —
    enforced here with a model validator (422 on violation), mirroring
    the DB CHECK so the constraint never surfaces as a 500.
    """

    model_config = ConfigDict(extra="forbid")

    name: MgmtName
    department: Department
    scope: KpiScope
    team_member_id: uuid.UUID | None = None
    unit: NonEmptyText
    cadence: KpiCadence
    direction: KpiDirection
    baseline: Decimal | None = None
    target: Decimal | None = None
    rationale_md: str | None = None

    @model_validator(mode="after")
    def _scope_pairs_with_team_member(self) -> MgmtKpiCreate:
        if (self.scope == "individual") != (self.team_member_id is not None):
            raise ValueError(
                "scope='individual' requires team_member_id; scope='department' forbids it"
            )
        return self


class MgmtKpiUpdate(BaseModel):
    """``PATCH /api/v1/management/kpis/{id}`` body — every field optional.

    Scope/team-member consistency is re-checked by the handler on the
    *resulting* row state (a PATCH may change either side of the pair),
    returning 422 on violation.
    """

    model_config = ConfigDict(extra="forbid")

    name: MgmtName | None = None
    department: Department | None = None
    scope: KpiScope | None = None
    team_member_id: uuid.UUID | None = None
    unit: NonEmptyText | None = None
    cadence: KpiCadence | None = None
    direction: KpiDirection | None = None
    baseline: Decimal | None = None
    target: Decimal | None = None
    rationale_md: str | None = None


class MgmtKpiRead(BaseModel):
    """``MgmtKpi`` wire shape — row columns plus computed fields.

    ``baseline`` / ``target`` and every computed value are JSON strings
    (Decimal-as-string per CLAUDE.md). ``latest_period`` /
    ``latest_value`` / ``previous_value`` come from the newest two
    datapoints by period; ``attainment_pct`` is
    ``latest_value / target * 100`` (rounded to 1 dp) for
    ``higher_is_better`` and ``target / latest_value * 100`` for
    ``lower_is_better``, so >100% always means "beating target". Null
    when there is no target, no datapoint, or the divisor is zero.
    """

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    department: str
    scope: str
    team_member_id: uuid.UUID | None = None
    unit: str
    cadence: str
    direction: str
    baseline: str | None = None
    target: str | None = None
    rationale_md: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None

    # Computed fields (see class docstring).
    latest_period: str | None = None
    latest_value: str | None = None
    previous_value: str | None = None
    datapoint_count: int = 0
    attainment_pct: str | None = None


# ---------------------------------------------------------------------------
# Datapoints
# ---------------------------------------------------------------------------


class MgmtKpiDatapointCreate(BaseModel):
    """``POST /api/v1/management/kpis/{id}/datapoints`` body.

    ``period`` format ('YYYY-MM' monthly / 'YYYY-Qn' quarterly) is
    validated by the handler against the parent KPI's cadence — a
    format/cadence mismatch is a 422. A duplicate period is a 409
    unless ``?overwrite=true``.
    """

    model_config = ConfigDict(extra="forbid")

    period: NonEmptyText
    value: Decimal
    note_md: str | None = None


class MgmtKpiDatapointRead(BaseModel):
    """``MgmtKpiDatapoint`` wire shape (``value`` is a JSON string)."""

    id: uuid.UUID
    kpi_id: uuid.UUID
    period: str
    value: str
    note_md: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------------------
# Series + dashboard composites
# ---------------------------------------------------------------------------


class MgmtKpiSeriesPoint(BaseModel):
    """One chart point in the series feed (``value`` is a JSON string)."""

    period: str
    value: str
    note_md: str | None = None


class MgmtKpiSeriesRead(BaseModel):
    """``GET /api/v1/management/kpis/{id}/series`` response — the chart feed."""

    kpi: MgmtKpiRead
    datapoints: list[MgmtKpiSeriesPoint]


class MgmtDashboardDepartments(BaseModel):
    """Department-scope KPIs grouped by department."""

    legal: list[MgmtKpiRead]
    compliance: list[MgmtKpiRead]


class MgmtDashboardTeamEntry(BaseModel):
    """One team member with their individual-scope KPIs."""

    member: MgmtTeamMemberRead
    kpis: list[MgmtKpiRead]


class MgmtDashboardRead(BaseModel):
    """``GET /api/v1/management/dashboard`` response — one call for the UI.

    ``departments`` carries scope='department' KPIs only; ``team``
    carries every active team member with their individual-scope KPIs.
    """

    departments: MgmtDashboardDepartments
    team: list[MgmtDashboardTeamEntry]
