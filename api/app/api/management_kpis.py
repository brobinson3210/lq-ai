"""Management-KPIs endpoints — Management tab, KPIs module.

Surface (all under ``/api/v1/management``):

* ``POST/GET /management/team-members``                — roster CRUD.
* ``GET/PATCH/DELETE /management/team-members/{id}``   — single member;
  DELETE soft-deletes and tombstones the member's individual KPIs in
  the same transaction (so they disappear from every read surface).
* ``POST/GET /management/kpis``                        — KPI definitions,
  with optional ``?department=``, ``?scope=``, ``?team_member_id=``.
* ``GET/PATCH/DELETE /management/kpis/{id}``           — single KPI
  (DELETE soft, 204).
* ``POST /management/kpis/{id}/datapoints``            — record a value
  for one period. Upsert-by-period: an existing period returns 409
  unless ``?overwrite=true``, which updates in place (200) and audits
  as ``mgmt_kpi.datapoint_update``.
* ``GET /management/kpis/{id}/datapoints``             — ``?from=&to=``
  period-string range filter (lexicographic compare is chronological
  within one cadence).
* ``GET /management/kpis/{id}/series``                 — the chart feed:
  the KPI read shape plus its datapoints ordered by period asc.
* ``GET /management/dashboard``                        — one call for the
  dashboard UI: department-scope KPIs grouped by department, plus every
  active team member with their individual-scope KPIs.

**Per-user isolation.** Rows are scoped to ``owner_id`` exactly as
stakeholders are: cross-user access returns 404, not 403, to avoid
leaking existence, and admins see only their own rows too. Datapoint
access always resolves the parent KPI through the caller's scope first.

**Computed fields.** ``latest_period`` / ``latest_value`` /
``previous_value`` / ``datapoint_count`` on the KPI read shape (and
``kpi_count`` on the team-member shape) are derived with correlated
scalar subqueries in the same SELECT as the row(s) — one query for the
whole list, never N+1. ``attainment_pct`` is computed in Python from
``latest_value`` / ``target`` / ``direction``.

**Period validation.** ``period`` must be ``YYYY-MM`` for monthly KPIs
and ``YYYY-Qn`` for quarterly ones; a mismatch is a 422 (plain
``{"detail": ...}`` shape, same as the reserved-slug and tag guards).

Audit logging: PRD §5.3 — every mutation writes an ``audit_log`` row via
:func:`app.audit.audit_action`, riding the same transaction as the state
change (same pattern as stakeholders).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.api.dependencies import ActiveUser
from app.audit import audit_action
from app.db.session import get_db
from app.errors import Conflict, NotFound, ValidationError
from app.models.management_kpi import (
    MGMT_DEPARTMENTS,
    MGMT_KPI_SCOPES,
    MgmtKpi,
    MgmtKpiDatapoint,
    MgmtTeamMember,
)
from app.schemas.management_kpis import (
    MgmtDashboardDepartments,
    MgmtDashboardRead,
    MgmtDashboardTeamEntry,
    MgmtKpiCreate,
    MgmtKpiDatapointCreate,
    MgmtKpiDatapointRead,
    MgmtKpiRead,
    MgmtKpiSeriesPoint,
    MgmtKpiSeriesRead,
    MgmtKpiUpdate,
    MgmtTeamMemberCreate,
    MgmtTeamMemberRead,
    MgmtTeamMemberUpdate,
)

router = APIRouter(prefix="/management", tags=["management-kpis"])
log = logging.getLogger(__name__)

_MONTHLY_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_QUARTERLY_PERIOD_RE = re.compile(r"^\d{4}-Q[1-4]$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_id(value: str, *, param: str) -> uuid.UUID:
    """Reject non-UUID path ids with the domain 400 (same as projects)."""

    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(
            f"{param} must be a UUID",
            details={param: value},
        ) from exc


def _validate_period(period: str, cadence: str, *, param: str = "period") -> None:
    """422 unless ``period`` matches the KPI's cadence format.

    Monthly KPIs use ``YYYY-MM``; quarterly KPIs use ``YYYY-Qn`` (n in
    1-4). Plain HTTPException so the body is the conventional
    ``{"detail": ...}`` shape (matching the other domain 422 guards).
    """

    pattern = _MONTHLY_PERIOD_RE if cadence == "monthly" else _QUARTERLY_PERIOD_RE
    expected = "YYYY-MM" if cadence == "monthly" else "YYYY-Qn"
    if not pattern.match(period):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{param} {period!r} does not match the {expected!r} format "
                f"required by this KPI's {cadence!r} cadence."
            ),
        )


async def _load_visible_team_member(
    db: AsyncSession,
    team_member_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> MgmtTeamMember:
    """Load a team member scoped to the caller; 404 on miss / cross-user / deleted."""

    stmt = select(MgmtTeamMember).where(
        MgmtTeamMember.id == team_member_id,
        MgmtTeamMember.owner_id == owner_id,
        MgmtTeamMember.deleted_at.is_(None),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound(
            f"Team member {team_member_id} not found.",
            details={"team_member_id": str(team_member_id)},
        )
    return row


async def _load_visible_kpi(
    db: AsyncSession,
    kpi_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> MgmtKpi:
    """Load a KPI scoped to the caller; 404 on miss / cross-user / deleted."""

    stmt = select(MgmtKpi).where(
        MgmtKpi.id == kpi_id,
        MgmtKpi.owner_id == owner_id,
        MgmtKpi.deleted_at.is_(None),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound(
            f"KPI {kpi_id} not found.",
            details={"kpi_id": str(kpi_id)},
        )
    return row


def _annotated_member_select() -> Select[Any]:
    """SELECT team-member rows with the ``kpi_count`` aggregate attached."""

    kpi_count_sq = (
        select(func.count(MgmtKpi.id))
        .where(
            MgmtKpi.team_member_id == MgmtTeamMember.id,
            MgmtKpi.deleted_at.is_(None),
        )
        .correlate(MgmtTeamMember)
        .scalar_subquery()
    )
    return select(MgmtTeamMember, kpi_count_sq.label("kpi_count"))


def _annotated_kpi_select() -> Select[Any]:
    """SELECT KPI rows with the four datapoint aggregates attached.

    Correlated scalar subqueries keep this a single round-trip for any
    number of rows; each subquery resolves over the UNIQUE
    ``(kpi_id, period)`` index. Periods order lexicographically =
    chronologically within one cadence, so ``ORDER BY period DESC``
    picks the newest.
    """

    latest_period_sq = (
        select(MgmtKpiDatapoint.period)
        .where(MgmtKpiDatapoint.kpi_id == MgmtKpi.id)
        .correlate(MgmtKpi)
        .order_by(MgmtKpiDatapoint.period.desc())
        .limit(1)
        .scalar_subquery()
    )
    latest_value_sq = (
        select(MgmtKpiDatapoint.value)
        .where(MgmtKpiDatapoint.kpi_id == MgmtKpi.id)
        .correlate(MgmtKpi)
        .order_by(MgmtKpiDatapoint.period.desc())
        .limit(1)
        .scalar_subquery()
    )
    previous_value_sq = (
        select(MgmtKpiDatapoint.value)
        .where(MgmtKpiDatapoint.kpi_id == MgmtKpi.id)
        .correlate(MgmtKpi)
        .order_by(MgmtKpiDatapoint.period.desc())
        .offset(1)
        .limit(1)
        .scalar_subquery()
    )
    datapoint_count_sq = (
        select(func.count(MgmtKpiDatapoint.id))
        .where(MgmtKpiDatapoint.kpi_id == MgmtKpi.id)
        .correlate(MgmtKpi)
        .scalar_subquery()
    )
    return select(
        MgmtKpi,
        latest_period_sq.label("latest_period"),
        latest_value_sq.label("latest_value"),
        previous_value_sq.label("previous_value"),
        datapoint_count_sq.label("datapoint_count"),
    )


def _attainment_pct(
    direction: str,
    target: Decimal | None,
    latest_value: Decimal | None,
) -> str | None:
    """Latest-vs-target percentage, oriented so >100% beats target.

    ``higher_is_better``: latest/target*100. ``lower_is_better``:
    target/latest*100. None when either operand is missing or the
    divisor is zero. Rounded to 1 decimal place, serialized as a
    string (Decimal-as-string per CLAUDE.md).
    """

    if target is None or latest_value is None:
        return None
    if direction == "higher_is_better":
        numerator, divisor = latest_value, target
    else:
        numerator, divisor = target, latest_value
    if divisor == 0:
        return None
    pct = (numerator / divisor * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return format(pct, "f")


def _decimal_str(value: Decimal | None) -> str | None:
    """Fixed-point string for a Numeric ('1200000', never '1.20E+6').

    asyncpg reconstitutes trailing-zero NUMERICs in scientific notation;
    ``format(..., 'f')`` renders the same value in plain decimal form so
    the wire shape is stable regardless of how the DB driver spells it.
    """

    return None if value is None else format(value, "f")


def _to_member_read(row: MgmtTeamMember, kpi_count: int) -> MgmtTeamMemberRead:
    read = MgmtTeamMemberRead.model_validate(row)
    read.kpi_count = kpi_count
    return read


def _to_kpi_read(
    row: MgmtKpi,
    latest_period: str | None,
    latest_value: Decimal | None,
    previous_value: Decimal | None,
    datapoint_count: int,
) -> MgmtKpiRead:
    """Build the wire shape, stringifying Numerics + deriving attainment."""

    return MgmtKpiRead(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        department=row.department,
        scope=row.scope,
        team_member_id=row.team_member_id,
        unit=row.unit,
        cadence=row.cadence,
        direction=row.direction,
        baseline=_decimal_str(row.baseline),
        target=_decimal_str(row.target),
        rationale_md=row.rationale_md,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        latest_period=latest_period,
        latest_value=_decimal_str(latest_value),
        previous_value=_decimal_str(previous_value),
        datapoint_count=datapoint_count,
        attainment_pct=_attainment_pct(row.direction, row.target, latest_value),
    )


async def _read_one_member(db: AsyncSession, team_member_id: uuid.UUID) -> MgmtTeamMemberRead:
    """Fetch one member (already scope-checked) with ``kpi_count``."""

    stmt = _annotated_member_select().where(MgmtTeamMember.id == team_member_id)
    row, kpi_count = (await db.execute(stmt)).one()
    return _to_member_read(row, kpi_count)


async def _read_one_kpi(db: AsyncSession, kpi_id: uuid.UUID) -> MgmtKpiRead:
    """Fetch one KPI (already scope-checked) with computed fields."""

    stmt = _annotated_kpi_select().where(MgmtKpi.id == kpi_id)
    row, latest_period, latest_value, previous_value, count = (await db.execute(stmt)).one()
    return _to_kpi_read(row, latest_period, latest_value, previous_value, count)


def _to_datapoint_read(row: MgmtKpiDatapoint) -> MgmtKpiDatapointRead:
    return MgmtKpiDatapointRead(
        id=row.id,
        kpi_id=row.kpi_id,
        period=row.period,
        value=format(row.value, "f"),
        note_md=row.note_md,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Team-member CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/team-members",
    response_model=MgmtTeamMemberRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a team member to the caller's roster",
)
async def create_team_member(
    payload: MgmtTeamMemberCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtTeamMemberRead:
    member = MgmtTeamMember(owner_id=user.id, **payload.model_dump())
    db.add(member)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_team_member.create",
        resource_type="mgmt_team_member",
        resource_id=str(member.id),
        request=request,
        details={"name": member.name, "department": member.department},
    )
    await db.commit()
    await db.refresh(member)

    log.info(
        "mgmt team member created",
        extra={
            "event": "mgmt_team_member_created",
            "user_id": str(user.id),
            "team_member_id": str(member.id),
        },
    )

    return await _read_one_member(db, member.id)


@router.get(
    "/team-members",
    response_model=list[MgmtTeamMemberRead],
    summary="List the caller's team roster",
    description=(
        "Active (non-deleted) team members owned by the caller, newest "
        "first, each with ``kpi_count`` (active KPIs assigned)."
    ),
)
async def list_team_members(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MgmtTeamMemberRead]:
    stmt = (
        _annotated_member_select()
        .where(
            MgmtTeamMember.owner_id == user.id,
            MgmtTeamMember.deleted_at.is_(None),
        )
        .order_by(MgmtTeamMember.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [_to_member_read(row, kpi_count) for row, kpi_count in rows]


@router.get(
    "/team-members/{team_member_id}",
    response_model=MgmtTeamMemberRead,
    summary="Fetch a single team member (owner-only)",
    responses={404: {"description": "Team member not found"}},
)
async def get_team_member(
    team_member_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtTeamMemberRead:
    mid = _validate_id(team_member_id, param="team_member_id")
    member = await _load_visible_team_member(db, mid, user.id)
    return await _read_one_member(db, member.id)


@router.patch(
    "/team-members/{team_member_id}",
    response_model=MgmtTeamMemberRead,
    summary="Partial update of a team member (owner-only)",
    responses={404: {"description": "Team member not found"}},
)
async def update_team_member(
    team_member_id: str,
    payload: MgmtTeamMemberUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtTeamMemberRead:
    mid = _validate_id(team_member_id, param="team_member_id")
    member = await _load_visible_team_member(db, mid, user.id)

    update_fields = payload.model_dump(exclude_unset=True)
    changed: list[str] = []
    for field, value in update_fields.items():
        if getattr(member, field) != value:
            setattr(member, field, value)
            changed.append(field)

    if changed:
        member.updated_at = datetime.now(tz=UTC)
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_team_member.update",
            resource_type="mgmt_team_member",
            resource_id=str(member.id),
            request=request,
            details={"changed_fields": sorted(changed)},
        )
        await db.commit()
        await db.refresh(member)

        log.info(
            "mgmt team member updated",
            extra={
                "event": "mgmt_team_member_updated",
                "user_id": str(user.id),
                "team_member_id": str(member.id),
                "fields": sorted(changed),
            },
        )

    return await _read_one_member(db, member.id)


@router.delete(
    "/team-members/{team_member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a team member (owner-only)",
    description=(
        "Sets ``deleted_at`` on the member AND on their active "
        "individual-scope KPIs in the same transaction, so those KPIs "
        "disappear from every read surface (list, detail, dashboard). "
        "A second delete on an already-deleted member returns 404."
    ),
    response_class=Response,
    responses={404: {"description": "Team member not found"}},
)
async def delete_team_member(
    team_member_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    mid = _validate_id(team_member_id, param="team_member_id")
    member = await _load_visible_team_member(db, mid, user.id)

    now = datetime.now(tz=UTC)
    member.deleted_at = now

    # Tombstone-cascade: the member's individual KPIs go with them (the
    # DB-level CASCADE only fires on a future hard delete).
    kpi_stmt = select(MgmtKpi).where(
        MgmtKpi.team_member_id == mid,
        MgmtKpi.deleted_at.is_(None),
    )
    member_kpis = (await db.execute(kpi_stmt)).scalars().all()
    for kpi in member_kpis:
        kpi.deleted_at = now

    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_team_member.delete",
        resource_type="mgmt_team_member",
        resource_id=str(mid),
        request=request,
        details={"name": member.name, "kpis_tombstoned": len(member_kpis)},
    )
    await db.commit()

    log.info(
        "mgmt team member soft-deleted",
        extra={
            "event": "mgmt_team_member_deleted",
            "user_id": str(user.id),
            "team_member_id": str(mid),
            "kpis_tombstoned": len(member_kpis),
        },
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# KPI CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/kpis",
    response_model=MgmtKpiRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a KPI",
    responses={404: {"description": "Team member not found"}},
)
async def create_kpi(
    payload: MgmtKpiCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtKpiRead:
    # An individual KPI must point at one of the caller's own active
    # team members — cross-user / deleted / unknown ids collapse to 404.
    if payload.team_member_id is not None:
        await _load_visible_team_member(db, payload.team_member_id, user.id)

    kpi = MgmtKpi(owner_id=user.id, **payload.model_dump())
    db.add(kpi)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_kpi.create",
        resource_type="mgmt_kpi",
        resource_id=str(kpi.id),
        request=request,
        details={
            "name": kpi.name,
            "department": kpi.department,
            "scope": kpi.scope,
        },
    )
    await db.commit()
    await db.refresh(kpi)

    log.info(
        "mgmt kpi created",
        extra={
            "event": "mgmt_kpi_created",
            "user_id": str(user.id),
            "kpi_id": str(kpi.id),
        },
    )

    return await _read_one_kpi(db, kpi.id)


@router.get(
    "/kpis",
    response_model=list[MgmtKpiRead],
    summary="List the caller's KPIs",
    description=(
        "Active (non-deleted) KPIs owned by the caller, newest first. "
        "``department`` / ``scope`` / ``team_member_id`` filter the list."
    ),
)
async def list_kpis(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    department: Annotated[
        str | None,
        Query(description="Filter to one department (legal|compliance)."),
    ] = None,
    scope: Annotated[
        str | None,
        Query(description="Filter to one scope (department|individual)."),
    ] = None,
    team_member_id: Annotated[
        str | None,
        Query(description="Filter to one team member's individual KPIs."),
    ] = None,
) -> list[MgmtKpiRead]:
    conditions: list[ColumnElement[bool]] = [
        MgmtKpi.owner_id == user.id,
        MgmtKpi.deleted_at.is_(None),
    ]
    if department is not None:
        if department not in MGMT_DEPARTMENTS:
            raise ValidationError(
                f"Unknown department {department!r}.",
                details={"allowed": list(MGMT_DEPARTMENTS)},
            )
        conditions.append(MgmtKpi.department == department)
    if scope is not None:
        if scope not in MGMT_KPI_SCOPES:
            raise ValidationError(
                f"Unknown scope {scope!r}.",
                details={"allowed": list(MGMT_KPI_SCOPES)},
            )
        conditions.append(MgmtKpi.scope == scope)
    if team_member_id is not None:
        mid = _validate_id(team_member_id, param="team_member_id")
        conditions.append(MgmtKpi.team_member_id == mid)

    stmt = _annotated_kpi_select().where(*conditions).order_by(MgmtKpi.created_at.desc())
    rows = (await db.execute(stmt)).all()
    return [
        _to_kpi_read(row, latest_period, latest_value, previous_value, count)
        for row, latest_period, latest_value, previous_value, count in rows
    ]


@router.get(
    "/kpis/{kpi_id}",
    response_model=MgmtKpiRead,
    summary="Fetch a single KPI (owner-only)",
    responses={404: {"description": "KPI not found"}},
)
async def get_kpi(
    kpi_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtKpiRead:
    kid = _validate_id(kpi_id, param="kpi_id")
    kpi = await _load_visible_kpi(db, kid, user.id)
    return await _read_one_kpi(db, kpi.id)


@router.patch(
    "/kpis/{kpi_id}",
    response_model=MgmtKpiRead,
    summary="Partial update of a KPI (owner-only)",
    description=(
        "Scope/team-member consistency is enforced on the resulting row "
        "state: ``scope='individual'`` must end up paired with a "
        "``team_member_id`` (owned by the caller) and "
        "``scope='department'`` without one — otherwise 422."
    ),
    responses={
        404: {"description": "KPI or team member not found"},
        422: {"description": "scope/team_member_id pairing violated"},
    },
)
async def update_kpi(
    kpi_id: str,
    payload: MgmtKpiUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtKpiRead:
    kid = _validate_id(kpi_id, param="kpi_id")
    kpi = await _load_visible_kpi(db, kid, user.id)

    update_fields = payload.model_dump(exclude_unset=True)

    # Validate the RESULTING scope/team_member pairing before touching
    # the row, so the DB CHECK never surfaces as a 500.
    effective_scope = update_fields.get("scope", kpi.scope)
    effective_member_id = update_fields.get("team_member_id", kpi.team_member_id)
    if (effective_scope == "individual") != (effective_member_id is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=("scope='individual' requires team_member_id; scope='department' forbids it."),
        )
    if "team_member_id" in update_fields and effective_member_id is not None:
        await _load_visible_team_member(db, effective_member_id, user.id)

    changed: list[str] = []
    for field, value in update_fields.items():
        if getattr(kpi, field) != value:
            setattr(kpi, field, value)
            changed.append(field)

    if changed:
        kpi.updated_at = datetime.now(tz=UTC)
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_kpi.update",
            resource_type="mgmt_kpi",
            resource_id=str(kpi.id),
            request=request,
            details={"changed_fields": sorted(changed)},
        )
        await db.commit()
        await db.refresh(kpi)

        log.info(
            "mgmt kpi updated",
            extra={
                "event": "mgmt_kpi_updated",
                "user_id": str(user.id),
                "kpi_id": str(kpi.id),
                "fields": sorted(changed),
            },
        )

    return await _read_one_kpi(db, kpi.id)


@router.delete(
    "/kpis/{kpi_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a KPI (owner-only)",
    description=(
        "Sets ``deleted_at`` on the row; datapoints are retained (they "
        "hard-CASCADE only on a future hard delete). A second delete on "
        "an already-deleted KPI returns 404."
    ),
    response_class=Response,
    responses={404: {"description": "KPI not found"}},
)
async def delete_kpi(
    kpi_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    kid = _validate_id(kpi_id, param="kpi_id")
    kpi = await _load_visible_kpi(db, kid, user.id)

    kpi.deleted_at = datetime.now(tz=UTC)
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_kpi.delete",
        resource_type="mgmt_kpi",
        resource_id=str(kid),
        request=request,
        details={"name": kpi.name},
    )
    await db.commit()

    log.info(
        "mgmt kpi soft-deleted",
        extra={
            "event": "mgmt_kpi_deleted",
            "user_id": str(user.id),
            "kpi_id": str(kid),
        },
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Datapoints
# ---------------------------------------------------------------------------


@router.post(
    "/kpis/{kpi_id}/datapoints",
    response_model=MgmtKpiDatapointRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a KPI value for one period (upsert-by-period)",
    description=(
        "Creates the datapoint (201). If the period already has a value, "
        "returns 409 unless ``?overwrite=true`` — then the existing row "
        "is updated in place (200) and audited as "
        "``mgmt_kpi.datapoint_update``. ``period`` must be ``YYYY-MM`` "
        "for monthly KPIs and ``YYYY-Qn`` for quarterly ones (422 on "
        "mismatch)."
    ),
    responses={
        404: {"description": "KPI not found"},
        409: {"description": "Period already recorded (and overwrite not set)"},
        422: {"description": "period format does not match the KPI's cadence"},
    },
)
async def create_datapoint(
    kpi_id: str,
    payload: MgmtKpiDatapointCreate,
    request: Request,
    response: Response,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    overwrite: Annotated[
        bool,
        Query(description="Update the existing row when the period already has a value."),
    ] = False,
) -> MgmtKpiDatapointRead:
    kid = _validate_id(kpi_id, param="kpi_id")
    kpi = await _load_visible_kpi(db, kid, user.id)
    _validate_period(payload.period, kpi.cadence)

    existing_stmt = select(MgmtKpiDatapoint).where(
        MgmtKpiDatapoint.kpi_id == kid,
        MgmtKpiDatapoint.period == payload.period,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()

    if existing is not None:
        if not overwrite:
            raise Conflict(
                f"KPI {kid} already has a value for period {payload.period!r}; "
                "pass ?overwrite=true to replace it.",
                details={"kpi_id": str(kid), "period": payload.period},
            )
        existing.value = payload.value
        existing.note_md = payload.note_md
        existing.updated_at = datetime.now(tz=UTC)
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_kpi.datapoint_update",
            resource_type="mgmt_kpi_datapoint",
            resource_id=str(existing.id),
            request=request,
            details={"kpi_id": str(kid), "period": payload.period},
        )
        await db.commit()
        await db.refresh(existing)

        log.info(
            "mgmt kpi datapoint overwritten",
            extra={
                "event": "mgmt_kpi_datapoint_updated",
                "user_id": str(user.id),
                "kpi_id": str(kid),
                "period": payload.period,
            },
        )

        response.status_code = status.HTTP_200_OK
        return _to_datapoint_read(existing)

    datapoint = MgmtKpiDatapoint(kpi_id=kid, **payload.model_dump())
    db.add(datapoint)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_kpi.datapoint_create",
        resource_type="mgmt_kpi_datapoint",
        resource_id=str(datapoint.id),
        request=request,
        details={"kpi_id": str(kid), "period": payload.period},
    )
    await db.commit()
    await db.refresh(datapoint)

    log.info(
        "mgmt kpi datapoint created",
        extra={
            "event": "mgmt_kpi_datapoint_created",
            "user_id": str(user.id),
            "kpi_id": str(kid),
            "period": payload.period,
        },
    )

    return _to_datapoint_read(datapoint)


@router.get(
    "/kpis/{kpi_id}/datapoints",
    response_model=list[MgmtKpiDatapointRead],
    summary="List a KPI's datapoints (period ascending)",
    description=(
        "``from`` / ``to`` bound the period range inclusively. Period "
        "strings order lexicographically = chronologically within one "
        "cadence, so the range filter is a plain string compare. Bounds "
        "must match the KPI's period format (422 on mismatch)."
    ),
    responses={
        404: {"description": "KPI not found"},
        422: {"description": "from/to format does not match the KPI's cadence"},
    },
)
async def list_datapoints(
    kpi_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    from_period: Annotated[
        str | None,
        Query(alias="from", description="Inclusive lower period bound."),
    ] = None,
    to_period: Annotated[
        str | None,
        Query(alias="to", description="Inclusive upper period bound."),
    ] = None,
) -> list[MgmtKpiDatapointRead]:
    kid = _validate_id(kpi_id, param="kpi_id")
    kpi = await _load_visible_kpi(db, kid, user.id)

    conditions: list[ColumnElement[bool]] = [MgmtKpiDatapoint.kpi_id == kid]
    if from_period is not None:
        _validate_period(from_period, kpi.cadence, param="from")
        conditions.append(MgmtKpiDatapoint.period >= from_period)
    if to_period is not None:
        _validate_period(to_period, kpi.cadence, param="to")
        conditions.append(MgmtKpiDatapoint.period <= to_period)

    stmt = select(MgmtKpiDatapoint).where(*conditions).order_by(MgmtKpiDatapoint.period.asc())
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_datapoint_read(r) for r in rows]


@router.get(
    "/kpis/{kpi_id}/series",
    response_model=MgmtKpiSeriesRead,
    summary="Chart feed for one KPI (definition + ordered datapoints)",
    responses={404: {"description": "KPI not found"}},
)
async def get_kpi_series(
    kpi_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtKpiSeriesRead:
    kid = _validate_id(kpi_id, param="kpi_id")
    await _load_visible_kpi(db, kid, user.id)

    kpi_read = await _read_one_kpi(db, kid)
    stmt = (
        select(MgmtKpiDatapoint)
        .where(MgmtKpiDatapoint.kpi_id == kid)
        .order_by(MgmtKpiDatapoint.period.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return MgmtKpiSeriesRead(
        kpi=kpi_read,
        datapoints=[
            MgmtKpiSeriesPoint(period=r.period, value=format(r.value, "f"), note_md=r.note_md)
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard",
    response_model=MgmtDashboardRead,
    summary="One-call dashboard feed (departments + team)",
    description=(
        "``departments`` groups the caller's scope='department' KPIs by "
        "department; ``team`` lists every active team member with their "
        "individual-scope KPIs. Two batched queries total — computed "
        "fields ride correlated subqueries, never per-row round-trips."
    ),
)
async def get_dashboard(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtDashboardRead:
    # Query 1: every active KPI with computed columns, one round-trip.
    kpi_stmt = (
        _annotated_kpi_select()
        .where(
            MgmtKpi.owner_id == user.id,
            MgmtKpi.deleted_at.is_(None),
        )
        .order_by(MgmtKpi.name.asc(), MgmtKpi.created_at.asc())
    )
    kpi_rows = (await db.execute(kpi_stmt)).all()

    departments: dict[str, list[MgmtKpiRead]] = {"legal": [], "compliance": []}
    by_member: dict[uuid.UUID, list[MgmtKpiRead]] = {}
    for row, latest_period, latest_value, previous_value, count in kpi_rows:
        read = _to_kpi_read(row, latest_period, latest_value, previous_value, count)
        if row.scope == "department":
            departments[row.department].append(read)
        elif row.team_member_id is not None:
            by_member.setdefault(row.team_member_id, []).append(read)

    # Query 2: every active team member with kpi_count, one round-trip.
    # Soft-deleted members are excluded here, and their individual KPIs
    # were tombstoned with them — so neither appears anywhere above.
    member_stmt = (
        _annotated_member_select()
        .where(
            MgmtTeamMember.owner_id == user.id,
            MgmtTeamMember.deleted_at.is_(None),
        )
        .order_by(MgmtTeamMember.name.asc(), MgmtTeamMember.created_at.asc())
    )
    member_rows = (await db.execute(member_stmt)).all()

    team = [
        MgmtDashboardTeamEntry(
            member=_to_member_read(member, kpi_count),
            kpis=by_member.get(member.id, []),
        )
        for member, kpi_count in member_rows
    ]

    return MgmtDashboardRead(
        departments=MgmtDashboardDepartments(
            legal=departments["legal"],
            compliance=departments["compliance"],
        ),
        team=team,
    )


__all__ = [
    "create_datapoint",
    "create_kpi",
    "create_team_member",
    "delete_kpi",
    "delete_team_member",
    "get_dashboard",
    "get_kpi",
    "get_kpi_series",
    "get_team_member",
    "list_datapoints",
    "list_kpis",
    "list_team_members",
    "router",
    "update_kpi",
    "update_team_member",
]
