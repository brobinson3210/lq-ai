"""Pydantic schemas for the Management-documents surface (Management tab).

Wire shapes for ``/api/v1/management/documents*``. The ORM model lives in
``app.models.management_document``; this module is the request/response
surface.

Conventions (matching ``app.schemas.management_kpis``):

* Write models (Create/Update) use ``extra="forbid"`` so typos in field
  names fail loudly instead of being silently dropped.
* Update models make every field optional; the handlers apply
  ``model_dump(exclude_unset=True)`` so "absent" and "explicit null"
  are distinguishable.
* The list shape (:class:`MgmtDocumentListRead`) deliberately **omits**
  ``content_md`` — board packs can be long, and the list endpoint is a
  metadata surface. It carries the computed ``content_chars`` instead
  (character count of the body, derived in the same SELECT as the
  rows). The detail shape (:class:`MgmtDocumentRead`) extends it with
  the full ``content_md``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

TITLE_MAX_LEN: int = 300
"""Hard cap on ``title``. Matches ``chk_mgmt_documents_title_len``."""

DOC_TYPE_MAX_LEN: int = 60
"""Hard cap on ``doc_type``. Matches ``chk_mgmt_documents_doc_type_len``."""

AUTHOR_MAX_LEN: int = 200
"""Hard cap on ``author``. Matches ``chk_mgmt_documents_author_len``."""

DocTitle = Annotated[
    str,
    StringConstraints(min_length=1, max_length=TITLE_MAX_LEN, strip_whitespace=True),
]
"""1-300 chars, leading/trailing whitespace stripped."""

DocType = Annotated[
    str,
    StringConstraints(min_length=1, max_length=DOC_TYPE_MAX_LEN, strip_whitespace=True),
]
"""Free-text document type, 1-60 chars — NOT an enum (open vocabulary)."""

DocAuthor = Annotated[str, StringConstraints(max_length=AUTHOR_MAX_LEN)]
"""Optional author, capped at 200 chars."""

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
"""Required free-text fields (``content_md``)."""


class MgmtDocumentCreate(BaseModel):
    """``POST /api/v1/management/documents`` body."""

    model_config = ConfigDict(extra="forbid")

    title: DocTitle
    doc_type: DocType
    content_md: NonEmptyText
    doc_date: datetime.date | None = None
    author: DocAuthor | None = None
    related_tags: str | None = None


class MgmtDocumentUpdate(BaseModel):
    """``PATCH /api/v1/management/documents/{id}`` body — all optional.

    The handler applies ``model_dump(exclude_unset=True)`` so nullable
    fields (``doc_date``, ``author``, ``related_tags``) can be
    explicitly cleared with ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    title: DocTitle | None = None
    doc_type: DocType | None = None
    content_md: NonEmptyText | None = None
    doc_date: datetime.date | None = None
    author: DocAuthor | None = None
    related_tags: str | None = None


class MgmtDocumentListRead(BaseModel):
    """``MgmtDocument`` list shape — all metadata, NO ``content_md``.

    ``content_chars`` is the character count of the (omitted) body,
    computed with ``char_length(content_md)`` in the same SELECT as the
    rows — the list endpoint never transfers document bodies.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    doc_type: str
    doc_date: datetime.date | None = None
    author: str | None = None
    related_tags: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: datetime.datetime | None = None

    # Computed (see class docstring).
    content_chars: int = 0


class MgmtDocumentRead(MgmtDocumentListRead):
    """``MgmtDocument`` detail shape — everything, including the body."""

    content_md: str
