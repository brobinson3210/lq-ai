"""Stakeholders endpoints — Management tab, Stakeholders module.

Surface:

* ``POST   /api/v1/stakeholders``                       — create (201).
* ``GET    /api/v1/stakeholders``                       — list, with optional
  ``?stakeholder_type=``, ``?space=`` (a named group of types), and
  ``?needs_attention=true`` (rows whose ``days_since_last_interaction``
  exceeds their ``cadence_target_days``).
* ``GET    /api/v1/stakeholders/{stakeholder_id}``      — fetch single.
* ``PATCH  /api/v1/stakeholders/{stakeholder_id}``      — partial update.
* ``DELETE /api/v1/stakeholders/{stakeholder_id}``      — soft delete (204);
  already-deleted returns 404 (same idempotency posture as projects).

* ``POST/GET /api/v1/stakeholders/{id}/interactions``   — touchpoint log.
* ``POST/GET /api/v1/stakeholders/{id}/commitments``    — open-loop items.
* ``POST/GET /api/v1/stakeholders/{id}/positions``      — stance history;
  ``?latest=true`` collapses to the latest ``as_of`` row per topic.

* ``GET    /api/v1/stakeholder-commitments``            — rollup across all
  the caller's stakeholders (``?status=&direction=``), each row carrying
  ``stakeholder_id`` / ``full_name`` / ``stakeholder_type`` ("what do I
  owe the board this week").
* ``PATCH  /api/v1/stakeholder-commitments/{commitment_id}`` — flat update
  (status / description / due_date / direction).

**Per-user isolation.** Stakeholders are scoped to ``owner_id`` exactly as
projects are: cross-user access returns 404, not 403, to avoid leaking
existence, and admins see only their own rows too. Sub-resource access
always resolves the parent stakeholder through the caller's scope first.

**Computed fields.** ``last_interaction_at`` / ``days_since_last_interaction``
/ ``open_commitments_count`` on the stakeholder read shape are derived
with correlated aggregate subqueries in the same SELECT as the
stakeholder row(s) — one query for the whole list, never N+1.

Audit logging: PRD §5.3 — every mutation writes an ``audit_log`` row via
:func:`app.audit.audit_action`, riding the same transaction as the state
change (same pattern as saved-prompts).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.api.dependencies import ActiveUser
from app.audit import audit_action
from app.db.session import get_db
from app.errors import NotFound, ValidationError
from app.models.stakeholder import (
    Stakeholder,
    StakeholderCommitment,
    StakeholderInteraction,
    StakeholderPosition,
)
from app.schemas.stakeholders import (
    StakeholderCommitmentCreate,
    StakeholderCommitmentRead,
    StakeholderCommitmentRollupRead,
    StakeholderCommitmentUpdate,
    StakeholderCreate,
    StakeholderInteractionCreate,
    StakeholderInteractionRead,
    StakeholderPositionCreate,
    StakeholderPositionRead,
    StakeholderRead,
    StakeholderUpdate,
)

router = APIRouter(prefix="/stakeholders", tags=["stakeholders"])
commitments_router = APIRouter(prefix="/stakeholder-commitments", tags=["stakeholders"])
log = logging.getLogger(__name__)

# ``?space=`` filter — named UI groupings over stakeholder_type. Committee
# roles are a detail on a director (``committee_seats``), so "board" is
# just chair + directors, not per-committee types.
_SPACE_TYPE_GROUPS: dict[str, tuple[str, ...]] = {
    "board": ("board_chair", "director"),
    "c-suite": ("ceo", "c_suite_peer"),
    "investors": ("investor_sponsor", "lender"),
    "customers": ("customer",),
    "regulators": ("regulator", "auditor"),
    "outside-firms": ("outside_counsel",),
    "media": ("media",),
}


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


async def _load_visible_stakeholder(
    db: AsyncSession,
    stakeholder_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> Stakeholder:
    """Load a stakeholder row scoped to the caller; 404 on miss / cross-user / deleted.

    Cross-user access collapses into 404 deliberately — same posture as
    projects/files. Soft-deleted rows are invisible everywhere.
    """

    stmt = select(Stakeholder).where(
        Stakeholder.id == stakeholder_id,
        Stakeholder.owner_id == owner_id,
        Stakeholder.deleted_at.is_(None),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound(
            f"Stakeholder {stakeholder_id} not found.",
            details={"stakeholder_id": str(stakeholder_id)},
        )
    return row


def _annotated_select() -> Select[Any]:
    """SELECT stakeholder rows with the two aggregate columns attached.

    Correlated scalar subqueries keep this a single round-trip for any
    number of rows (the DB planner turns them into grouped lookups over
    the ``(stakeholder_id, occurred_at DESC)`` and
    ``(stakeholder_id, status)`` indexes).
    """

    last_interaction_sq = (
        select(func.max(StakeholderInteraction.occurred_at))
        .where(StakeholderInteraction.stakeholder_id == Stakeholder.id)
        .correlate(Stakeholder)
        .scalar_subquery()
    )
    open_commitments_sq = (
        select(func.count(StakeholderCommitment.id))
        .where(
            StakeholderCommitment.stakeholder_id == Stakeholder.id,
            StakeholderCommitment.status == "open",
        )
        .correlate(Stakeholder)
        .scalar_subquery()
    )
    return select(
        Stakeholder,
        last_interaction_sq.label("last_interaction_at"),
        open_commitments_sq.label("open_commitments_count"),
    )


def _to_read(
    row: Stakeholder,
    last_interaction_at: datetime | None,
    open_commitments_count: int,
) -> StakeholderRead:
    """Build the wire shape, deriving ``days_since_last_interaction``."""

    days_since: int | None = None
    if last_interaction_at is not None:
        days_since = max((datetime.now(tz=UTC) - last_interaction_at).days, 0)
    return StakeholderRead(
        id=row.id,
        owner_id=row.owner_id,
        full_name=row.full_name,
        organization=row.organization,
        role_title=row.role_title,
        stakeholder_type=row.stakeholder_type,
        committee_seats=row.committee_seats,
        overall_health=row.overall_health,
        cadence_target_days=row.cadence_target_days,
        interests_md=row.interests_md,
        communication_preferences_md=row.communication_preferences_md,
        notes_md=row.notes_md,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        last_interaction_at=last_interaction_at,
        days_since_last_interaction=days_since,
        open_commitments_count=open_commitments_count,
    )


async def _read_one(db: AsyncSession, stakeholder_id: uuid.UUID) -> StakeholderRead:
    """Fetch one stakeholder (already scope-checked) with computed fields."""

    stmt = _annotated_select().where(Stakeholder.id == stakeholder_id)
    row, last_at, open_count = (await db.execute(stmt)).one()
    return _to_read(row, last_at, open_count)


# ---------------------------------------------------------------------------
# Stakeholder CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=StakeholderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a stakeholder",
)
async def create_stakeholder(
    payload: StakeholderCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StakeholderRead:
    stakeholder = Stakeholder(owner_id=user.id, **payload.model_dump())
    db.add(stakeholder)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="stakeholder.create",
        resource_type="stakeholder",
        resource_id=str(stakeholder.id),
        request=request,
        details={
            "full_name": stakeholder.full_name,
            "stakeholder_type": stakeholder.stakeholder_type,
        },
    )
    await db.commit()
    await db.refresh(stakeholder)

    log.info(
        "stakeholder created",
        extra={
            "event": "stakeholder_created",
            "user_id": str(user.id),
            "stakeholder_id": str(stakeholder.id),
        },
    )

    return await _read_one(db, stakeholder.id)


@router.get(
    "",
    response_model=list[StakeholderRead],
    summary="List the caller's stakeholders",
    description=(
        "Active (non-deleted) stakeholders owned by the caller, newest "
        "first. ``stakeholder_type`` filters to one type; ``space`` "
        "filters to a named type group (board, c-suite, investors, "
        "customers, regulators, outside-firms, media); "
        "``needs_attention=true`` keeps only rows whose "
        "``days_since_last_interaction`` exceeds their "
        "``cadence_target_days``."
    ),
)
async def list_stakeholders(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    stakeholder_type: Annotated[
        str | None,
        Query(description="Filter to a single stakeholder_type."),
    ] = None,
    space: Annotated[
        str | None,
        Query(description="Filter to a named type group (e.g. 'board')."),
    ] = None,
    needs_attention: Annotated[
        bool,
        Query(description="Only rows past their contact cadence."),
    ] = False,
) -> list[StakeholderRead]:
    conditions: list[ColumnElement[bool]] = [
        Stakeholder.owner_id == user.id,
        Stakeholder.deleted_at.is_(None),
    ]

    if stakeholder_type is not None:
        from app.models.stakeholder import STAKEHOLDER_TYPES

        if stakeholder_type not in STAKEHOLDER_TYPES:
            raise ValidationError(
                f"Unknown stakeholder_type {stakeholder_type!r}.",
                details={"allowed": list(STAKEHOLDER_TYPES)},
            )
        conditions.append(Stakeholder.stakeholder_type == stakeholder_type)

    if space is not None:
        group = _SPACE_TYPE_GROUPS.get(space)
        if group is None:
            raise ValidationError(
                f"Unknown space {space!r}.",
                details={"allowed": sorted(_SPACE_TYPE_GROUPS)},
            )
        conditions.append(Stakeholder.stakeholder_type.in_(group))

    stmt = _annotated_select().where(*conditions).order_by(Stakeholder.created_at.desc())
    rows = (await db.execute(stmt)).all()
    results = [_to_read(row, last_at, open_count) for row, last_at, open_count in rows]

    if needs_attention:
        # Only rows measurably past their cadence qualify — a stakeholder
        # with no cadence target (or no interactions yet, so no
        # days-since value) is not flagged, per the module spec.
        results = [
            r
            for r in results
            if r.cadence_target_days is not None
            and r.days_since_last_interaction is not None
            and r.days_since_last_interaction > r.cadence_target_days
        ]

    return results


@router.get(
    "/{stakeholder_id}",
    response_model=StakeholderRead,
    summary="Fetch a single stakeholder (owner-only)",
    responses={404: {"description": "Stakeholder not found"}},
)
async def get_stakeholder(
    stakeholder_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StakeholderRead:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    stakeholder = await _load_visible_stakeholder(db, sid, user.id)
    return await _read_one(db, stakeholder.id)


@router.patch(
    "/{stakeholder_id}",
    response_model=StakeholderRead,
    summary="Partial update of a stakeholder (owner-only)",
    responses={404: {"description": "Stakeholder not found"}},
)
async def update_stakeholder(
    stakeholder_id: str,
    payload: StakeholderUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StakeholderRead:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    stakeholder = await _load_visible_stakeholder(db, sid, user.id)

    update_fields = payload.model_dump(exclude_unset=True)
    changed: list[str] = []
    for field, value in update_fields.items():
        if getattr(stakeholder, field) != value:
            setattr(stakeholder, field, value)
            changed.append(field)

    if changed:
        stakeholder.updated_at = datetime.now(tz=UTC)
        await audit_action(
            db,
            user_id=user.id,
            action="stakeholder.update",
            resource_type="stakeholder",
            resource_id=str(stakeholder.id),
            request=request,
            details={"changed_fields": sorted(changed)},
        )
        await db.commit()
        await db.refresh(stakeholder)

        log.info(
            "stakeholder updated",
            extra={
                "event": "stakeholder_updated",
                "user_id": str(user.id),
                "stakeholder_id": str(stakeholder.id),
                "fields": sorted(changed),
            },
        )

    return await _read_one(db, stakeholder.id)


@router.delete(
    "/{stakeholder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a stakeholder (owner-only)",
    description=(
        "Sets ``deleted_at`` on the row; child interactions / commitments "
        "/ positions are retained (they hard-CASCADE only on a future "
        "hard delete). A second delete on an already-deleted stakeholder "
        "returns 404."
    ),
    response_class=Response,
    responses={404: {"description": "Stakeholder not found"}},
)
async def delete_stakeholder(
    stakeholder_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    stakeholder = await _load_visible_stakeholder(db, sid, user.id)

    stakeholder.deleted_at = datetime.now(tz=UTC)
    await audit_action(
        db,
        user_id=user.id,
        action="stakeholder.delete",
        resource_type="stakeholder",
        resource_id=str(sid),
        request=request,
        details={"full_name": stakeholder.full_name},
    )
    await db.commit()

    log.info(
        "stakeholder soft-deleted",
        extra={
            "event": "stakeholder_deleted",
            "user_id": str(user.id),
            "stakeholder_id": str(sid),
        },
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Sub-resource: interactions
# ---------------------------------------------------------------------------


@router.post(
    "/{stakeholder_id}/interactions",
    response_model=StakeholderInteractionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Log an interaction with a stakeholder",
    responses={404: {"description": "Stakeholder not found"}},
)
async def create_interaction(
    stakeholder_id: str,
    payload: StakeholderInteractionCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StakeholderInteractionRead:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    await _load_visible_stakeholder(db, sid, user.id)

    interaction = StakeholderInteraction(stakeholder_id=sid, **payload.model_dump())
    db.add(interaction)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="stakeholder.interaction_create",
        resource_type="stakeholder_interaction",
        resource_id=str(interaction.id),
        request=request,
        details={"stakeholder_id": str(sid), "channel": interaction.channel},
    )
    await db.commit()
    await db.refresh(interaction)

    return StakeholderInteractionRead.model_validate(interaction)


@router.get(
    "/{stakeholder_id}/interactions",
    response_model=list[StakeholderInteractionRead],
    summary="List a stakeholder's interactions (newest first)",
    responses={404: {"description": "Stakeholder not found"}},
)
async def list_interactions(
    stakeholder_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[StakeholderInteractionRead]:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    await _load_visible_stakeholder(db, sid, user.id)

    stmt = (
        select(StakeholderInteraction)
        .where(StakeholderInteraction.stakeholder_id == sid)
        .order_by(
            StakeholderInteraction.occurred_at.desc(),
            StakeholderInteraction.id.desc(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [StakeholderInteractionRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Sub-resource: commitments
# ---------------------------------------------------------------------------


@router.post(
    "/{stakeholder_id}/commitments",
    response_model=StakeholderCommitmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a commitment for a stakeholder",
    responses={404: {"description": "Stakeholder not found"}},
)
async def create_commitment(
    stakeholder_id: str,
    payload: StakeholderCommitmentCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StakeholderCommitmentRead:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    await _load_visible_stakeholder(db, sid, user.id)

    commitment = StakeholderCommitment(stakeholder_id=sid, **payload.model_dump())
    db.add(commitment)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="stakeholder.commitment_create",
        resource_type="stakeholder_commitment",
        resource_id=str(commitment.id),
        request=request,
        details={"stakeholder_id": str(sid), "direction": commitment.direction},
    )
    await db.commit()
    await db.refresh(commitment)

    return StakeholderCommitmentRead.model_validate(commitment)


@router.get(
    "/{stakeholder_id}/commitments",
    response_model=list[StakeholderCommitmentRead],
    summary="List a stakeholder's commitments",
    description=(
        "Ordered by due date (soonest first, undated last), then newest. "
        "``status`` optionally filters to one lifecycle state."
    ),
    responses={404: {"description": "Stakeholder not found"}},
)
async def list_commitments(
    stakeholder_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter to one status (open|done|dropped)."),
    ] = None,
) -> list[StakeholderCommitmentRead]:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    await _load_visible_stakeholder(db, sid, user.id)

    conditions: list[ColumnElement[bool]] = [StakeholderCommitment.stakeholder_id == sid]
    if status_filter is not None:
        _check_commitment_status(status_filter)
        conditions.append(StakeholderCommitment.status == status_filter)

    stmt = (
        select(StakeholderCommitment)
        .where(*conditions)
        .order_by(
            StakeholderCommitment.due_date.asc().nulls_last(),
            StakeholderCommitment.created_at.desc(),
            StakeholderCommitment.id.desc(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [StakeholderCommitmentRead.model_validate(r) for r in rows]


def _check_commitment_status(value: str) -> None:
    from app.models.stakeholder import COMMITMENT_STATUSES

    if value not in COMMITMENT_STATUSES:
        raise ValidationError(
            f"Unknown status {value!r}.",
            details={"allowed": list(COMMITMENT_STATUSES)},
        )


def _check_commitment_direction(value: str) -> None:
    from app.models.stakeholder import COMMITMENT_DIRECTIONS

    if value not in COMMITMENT_DIRECTIONS:
        raise ValidationError(
            f"Unknown direction {value!r}.",
            details={"allowed": list(COMMITMENT_DIRECTIONS)},
        )


# ---------------------------------------------------------------------------
# Sub-resource: positions
# ---------------------------------------------------------------------------


@router.post(
    "/{stakeholder_id}/positions",
    response_model=StakeholderPositionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a stakeholder's stance on a topic (append-only history)",
    responses={404: {"description": "Stakeholder not found"}},
)
async def create_position(
    stakeholder_id: str,
    payload: StakeholderPositionCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StakeholderPositionRead:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    await _load_visible_stakeholder(db, sid, user.id)

    position = StakeholderPosition(stakeholder_id=sid, **payload.model_dump())
    db.add(position)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="stakeholder.position_create",
        resource_type="stakeholder_position",
        resource_id=str(position.id),
        request=request,
        details={
            "stakeholder_id": str(sid),
            "topic": position.topic,
            "stance": position.stance,
        },
    )
    await db.commit()
    await db.refresh(position)

    return StakeholderPositionRead.model_validate(position)


@router.get(
    "/{stakeholder_id}/positions",
    response_model=list[StakeholderPositionRead],
    summary="List a stakeholder's stance history",
    description=(
        "Full history by default, grouped by topic with the newest "
        "``as_of`` first inside each topic. ``latest=true`` collapses to "
        "one row per topic — the latest ``as_of`` wins (ties broken by "
        "``created_at``)."
    ),
    responses={404: {"description": "Stakeholder not found"}},
)
async def list_positions(
    stakeholder_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    latest: Annotated[
        bool,
        Query(description="Return only the latest stance per topic."),
    ] = False,
) -> list[StakeholderPositionRead]:
    sid = _validate_id(stakeholder_id, param="stakeholder_id")
    await _load_visible_stakeholder(db, sid, user.id)

    stmt = (
        select(StakeholderPosition)
        .where(StakeholderPosition.stakeholder_id == sid)
        .order_by(
            StakeholderPosition.topic.asc(),
            StakeholderPosition.as_of.desc(),
            StakeholderPosition.created_at.desc(),
            StakeholderPosition.id.desc(),
        )
    )
    if latest:
        # Postgres DISTINCT ON — the ORDER BY above puts the winning row
        # (latest as_of, then latest created_at) first within each topic.
        stmt = stmt.distinct(StakeholderPosition.topic)
    rows = (await db.execute(stmt)).scalars().all()
    return [StakeholderPositionRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Flat commitments surface: rollup + PATCH
# ---------------------------------------------------------------------------


@commitments_router.get(
    "",
    response_model=list[StakeholderCommitmentRollupRead],
    summary="Commitments rollup across all the caller's stakeholders",
    description=(
        '"What do I owe the board this week" — every commitment across '
        "the caller's active stakeholders, optionally filtered by "
        "``status`` and/or ``direction``. Each row carries the "
        "stakeholder's ``full_name`` and ``stakeholder_type``. Ordered by "
        "due date (soonest first, undated last), then newest."
    ),
)
async def rollup_commitments(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter to one status (open|done|dropped)."),
    ] = None,
    direction: Annotated[
        str | None,
        Query(description="Filter to one direction (we_owe|they_owe)."),
    ] = None,
) -> list[StakeholderCommitmentRollupRead]:
    conditions: list[ColumnElement[bool]] = [
        Stakeholder.owner_id == user.id,
        Stakeholder.deleted_at.is_(None),
    ]
    if status_filter is not None:
        _check_commitment_status(status_filter)
        conditions.append(StakeholderCommitment.status == status_filter)
    if direction is not None:
        _check_commitment_direction(direction)
        conditions.append(StakeholderCommitment.direction == direction)

    stmt = (
        select(StakeholderCommitment, Stakeholder.full_name, Stakeholder.stakeholder_type)
        .join(Stakeholder, StakeholderCommitment.stakeholder_id == Stakeholder.id)
        .where(*conditions)
        .order_by(
            StakeholderCommitment.due_date.asc().nulls_last(),
            StakeholderCommitment.created_at.desc(),
            StakeholderCommitment.id.desc(),
        )
    )
    rows = (await db.execute(stmt)).all()
    return [
        StakeholderCommitmentRollupRead(
            id=commitment.id,
            stakeholder_id=commitment.stakeholder_id,
            direction=commitment.direction,
            description=commitment.description,
            due_date=commitment.due_date,
            status=commitment.status,
            created_at=commitment.created_at,
            updated_at=commitment.updated_at,
            full_name=full_name,
            stakeholder_type=stakeholder_type,
        )
        for commitment, full_name, stakeholder_type in rows
    ]


@commitments_router.patch(
    "/{commitment_id}",
    response_model=StakeholderCommitmentRead,
    summary="Update a commitment (status / description / due_date / direction)",
    responses={404: {"description": "Commitment not found"}},
)
async def update_commitment(
    commitment_id: str,
    payload: StakeholderCommitmentUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StakeholderCommitmentRead:
    cid = _validate_id(commitment_id, param="commitment_id")

    # Resolve through the caller's stakeholder scope — a commitment on
    # another user's stakeholder (or on a soft-deleted one) is invisible.
    stmt = (
        select(StakeholderCommitment)
        .join(Stakeholder, StakeholderCommitment.stakeholder_id == Stakeholder.id)
        .where(
            StakeholderCommitment.id == cid,
            Stakeholder.owner_id == user.id,
            Stakeholder.deleted_at.is_(None),
        )
    )
    commitment = (await db.execute(stmt)).scalar_one_or_none()
    if commitment is None:
        raise NotFound(
            f"Commitment {cid} not found.",
            details={"commitment_id": str(cid)},
        )

    update_fields = payload.model_dump(exclude_unset=True)
    changed: list[str] = []
    for field, value in update_fields.items():
        if getattr(commitment, field) != value:
            setattr(commitment, field, value)
            changed.append(field)

    if changed:
        commitment.updated_at = datetime.now(tz=UTC)
        await audit_action(
            db,
            user_id=user.id,
            action="stakeholder.commitment_update",
            resource_type="stakeholder_commitment",
            resource_id=str(commitment.id),
            request=request,
            details={
                "stakeholder_id": str(commitment.stakeholder_id),
                "changed_fields": sorted(changed),
            },
        )
        await db.commit()
        await db.refresh(commitment)

    return StakeholderCommitmentRead.model_validate(commitment)


__all__ = [
    "commitments_router",
    "create_commitment",
    "create_interaction",
    "create_position",
    "create_stakeholder",
    "delete_stakeholder",
    "get_stakeholder",
    "list_commitments",
    "list_interactions",
    "list_positions",
    "list_stakeholders",
    "rollup_commitments",
    "router",
    "update_commitment",
    "update_stakeholder",
]
