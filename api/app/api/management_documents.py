"""Management-documents endpoints — Management tab, Documents module.

Surface (all under ``/api/v1/management``):

* ``POST /management/documents``               — create (201).
* ``GET /management/documents``                — metadata list (no
  ``content_md``; ``content_chars`` instead), newest ``doc_date`` first
  (NULLs last, then ``created_at`` desc). Filters: ``?doc_type=``
  (exact), ``?q=`` (case-insensitive substring over title OR author OR
  related_tags — deliberately NOT the body, to keep the scan cheap),
  ``?date_from=`` / ``?date_to=`` (inclusive ``doc_date`` range).
* ``GET /management/documents/{id}``           — full read incl. body.
* ``PATCH /management/documents/{id}``         — partial update.
* ``DELETE /management/documents/{id}``        — soft delete (204).

**Per-user isolation.** Rows are scoped to ``owner_id`` exactly as the
KPIs module: cross-user access returns 404, not 403, to avoid leaking
existence, and admins see only their own rows too.

**v1 content model.** Documents are inline markdown (``content_md``
TEXT in Postgres), not binary uploads through the ingestion pipeline —
see the model docstring. The upcoming AI pre-meeting-brief feature
reads these rows with a plain SELECT.

Audit logging: PRD §5.3 — every mutation writes an ``audit_log`` row via
:func:`app.audit.audit_action`, riding the same transaction as the state
change (same pattern as stakeholders/KPIs).
"""

from __future__ import annotations

import datetime
import logging
import uuid
from datetime import UTC, datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.api.dependencies import ActiveUser
from app.audit import audit_action
from app.db.session import get_db
from app.errors import NotFound, ValidationError
from app.models.management_document import MgmtDocument
from app.schemas.management_documents import (
    MgmtDocumentCreate,
    MgmtDocumentListRead,
    MgmtDocumentRead,
    MgmtDocumentUpdate,
)

router = APIRouter(prefix="/management", tags=["management-documents"])
log = logging.getLogger(__name__)


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


async def _load_visible_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> MgmtDocument:
    """Load a document scoped to the caller; 404 on miss / cross-user / deleted."""

    stmt = select(MgmtDocument).where(
        MgmtDocument.id == document_id,
        MgmtDocument.owner_id == owner_id,
        MgmtDocument.deleted_at.is_(None),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound(
            f"Document {document_id} not found.",
            details={"document_id": str(document_id)},
        )
    return row


def _metadata_select() -> Select[Any]:
    """SELECT the list-shape columns only — the body never leaves the DB.

    ``char_length(content_md)`` computes ``content_chars`` in the same
    round-trip; the (potentially long) ``content_md`` itself is not in
    the column list, so list responses stay cheap regardless of corpus
    size.
    """

    return select(
        MgmtDocument.id,
        MgmtDocument.owner_id,
        MgmtDocument.title,
        MgmtDocument.doc_type,
        MgmtDocument.doc_date,
        MgmtDocument.author,
        MgmtDocument.related_tags,
        MgmtDocument.created_at,
        MgmtDocument.updated_at,
        MgmtDocument.deleted_at,
        func.char_length(MgmtDocument.content_md).label("content_chars"),
    )


def _to_read(row: MgmtDocument) -> MgmtDocumentRead:
    """Full detail shape; ``content_chars`` derives from the loaded body."""

    return MgmtDocumentRead(
        id=row.id,
        owner_id=row.owner_id,
        title=row.title,
        doc_type=row.doc_type,
        doc_date=row.doc_date,
        author=row.author,
        related_tags=row.related_tags,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        content_chars=len(row.content_md),
        content_md=row.content_md,
    )


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/documents",
    response_model=MgmtDocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Management-space document",
)
async def create_document(
    payload: MgmtDocumentCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtDocumentRead:
    document = MgmtDocument(owner_id=user.id, **payload.model_dump())
    db.add(document)
    await db.flush()

    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_document.create",
        resource_type="mgmt_document",
        resource_id=str(document.id),
        request=request,
        details={"title": document.title, "doc_type": document.doc_type},
    )
    await db.commit()
    await db.refresh(document)

    log.info(
        "mgmt document created",
        extra={
            "event": "mgmt_document_created",
            "user_id": str(user.id),
            "document_id": str(document.id),
        },
    )

    return _to_read(document)


@router.get(
    "/documents",
    response_model=list[MgmtDocumentListRead],
    summary="List the caller's documents (metadata only)",
    description=(
        "Active (non-deleted) documents owned by the caller, newest "
        "``doc_date`` first (NULL dates last, then ``created_at`` desc). "
        "The list shape omits ``content_md`` and carries "
        "``content_chars`` instead. ``doc_type`` filters exactly; ``q`` "
        "is a case-insensitive substring match over title, author, and "
        "related_tags (not the body); ``date_from`` / ``date_to`` bound "
        "``doc_date`` inclusively."
    ),
)
async def list_documents(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    doc_type: Annotated[
        str | None,
        Query(description="Filter to one document type (exact match)."),
    ] = None,
    q: Annotated[
        str | None,
        Query(
            description=(
                "Case-insensitive substring over title OR author OR related_tags (not the body)."
            ),
        ),
    ] = None,
    date_from: Annotated[
        datetime.date | None,
        Query(description="Inclusive lower doc_date bound (YYYY-MM-DD)."),
    ] = None,
    date_to: Annotated[
        datetime.date | None,
        Query(description="Inclusive upper doc_date bound (YYYY-MM-DD)."),
    ] = None,
) -> list[MgmtDocumentListRead]:
    conditions: list[ColumnElement[bool]] = [
        MgmtDocument.owner_id == user.id,
        MgmtDocument.deleted_at.is_(None),
    ]
    if doc_type is not None:
        conditions.append(MgmtDocument.doc_type == doc_type)
    if q is not None and q.strip():
        needle = q.strip()
        conditions.append(
            MgmtDocument.title.icontains(needle, autoescape=True)
            | MgmtDocument.author.icontains(needle, autoescape=True)
            | MgmtDocument.related_tags.icontains(needle, autoescape=True)
        )
    if date_from is not None:
        conditions.append(MgmtDocument.doc_date >= date_from)
    if date_to is not None:
        conditions.append(MgmtDocument.doc_date <= date_to)

    stmt = (
        _metadata_select()
        .where(*conditions)
        .order_by(
            MgmtDocument.doc_date.desc().nulls_last(),
            MgmtDocument.created_at.desc(),
        )
    )
    rows = (await db.execute(stmt)).all()
    return [MgmtDocumentListRead.model_validate(row) for row in rows]


@router.get(
    "/documents/{document_id}",
    response_model=MgmtDocumentRead,
    summary="Fetch a single document incl. content (owner-only)",
    responses={404: {"description": "Document not found"}},
)
async def get_document(
    document_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtDocumentRead:
    did = _validate_id(document_id, param="document_id")
    document = await _load_visible_document(db, did, user.id)
    return _to_read(document)


@router.patch(
    "/documents/{document_id}",
    response_model=MgmtDocumentRead,
    summary="Partial update of a document (owner-only)",
    responses={404: {"description": "Document not found"}},
)
async def update_document(
    document_id: str,
    payload: MgmtDocumentUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtDocumentRead:
    did = _validate_id(document_id, param="document_id")
    document = await _load_visible_document(db, did, user.id)

    update_fields = payload.model_dump(exclude_unset=True)
    changed: list[str] = []
    for field, value in update_fields.items():
        if getattr(document, field) != value:
            setattr(document, field, value)
            changed.append(field)

    if changed:
        document.updated_at = dt.now(tz=UTC)
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_document.update",
            resource_type="mgmt_document",
            resource_id=str(document.id),
            request=request,
            details={"changed_fields": sorted(changed)},
        )
        await db.commit()
        await db.refresh(document)

        log.info(
            "mgmt document updated",
            extra={
                "event": "mgmt_document_updated",
                "user_id": str(user.id),
                "document_id": str(document.id),
                "fields": sorted(changed),
            },
        )

    return _to_read(document)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a document (owner-only)",
    description=(
        "Sets ``deleted_at`` on the row; the content is retained in the "
        "DB (tombstoned, not erased). A second delete on an "
        "already-deleted document returns 404."
    ),
    response_class=Response,
    responses={404: {"description": "Document not found"}},
)
async def delete_document(
    document_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    did = _validate_id(document_id, param="document_id")
    document = await _load_visible_document(db, did, user.id)

    document.deleted_at = dt.now(tz=UTC)
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_document.delete",
        resource_type="mgmt_document",
        resource_id=str(did),
        request=request,
        details={"title": document.title, "doc_type": document.doc_type},
    )
    await db.commit()

    log.info(
        "mgmt document soft-deleted",
        extra={
            "event": "mgmt_document_deleted",
            "user_id": str(user.id),
            "document_id": str(did),
        },
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = [
    "create_document",
    "delete_document",
    "get_document",
    "list_documents",
    "router",
    "update_document",
]
