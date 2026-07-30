"""Management-documents ORM model — Management tab, Documents module.

Per-user (owner-scoped) private document space for executive work
product: board packs, meeting minutes, memos, briefing notes. One table
(migration ``0068_management_documents.py``):

* :class:`MgmtDocument` — one document per row. ``doc_type`` is **free
  text** (CHECK-bounded to 1..60 chars, NOT an enum): the corpus
  vocabulary is open — "board_pack", "minutes", "memo", "strategy note"
  all coexist without a migration. ``related_tags`` is a free-text
  comma-separated list of storyline/topic tags. ``doc_date`` is the
  user-asserted date of the document (board-meeting date, memo date),
  distinct from ``created_at`` (row insert time).

**v1 stores document content inline** in ``content_md`` (markdown TEXT
in Postgres), not as binary uploads through the ingestion pipeline —
the Management space holds text work product, and inline storage keeps
the AI pre-meeting-brief feature a simple SELECT away with zero
MinIO/worker coupling. Binary/PDF upload via the existing ingestion
pipeline is explicitly roadmap.

``mgmt_documents`` soft-deletes via ``deleted_at`` (NULL means active),
matching the stakeholders/KPIs posture. The class is named
``MgmtDocument`` (not ``Document``) because ``Document`` is already
taken in ``app.models`` by the ingestion-pipeline table.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MgmtDocument(Base):
    """One private Management-space document (inline markdown, v1).

    Owner-scoped: every read/write path filters on ``owner_id`` and
    cross-user access collapses to 404 (same posture as stakeholders
    and KPIs). ``deleted_at`` is the soft-delete tombstone — NULL means
    active.
    """

    __tablename__ = "mgmt_documents"
    __table_args__ = (
        CheckConstraint(
            "char_length(title) > 0 AND char_length(title) <= 300",
            name="chk_mgmt_documents_title_len",
        ),
        CheckConstraint(
            "char_length(doc_type) > 0 AND char_length(doc_type) <= 60",
            name="chk_mgmt_documents_doc_type_len",
        ),
        CheckConstraint(
            "author IS NULL OR char_length(author) <= 200",
            name="chk_mgmt_documents_author_len",
        ),
        Index("ix_mgmt_documents_owner_doc_type", "owner_id", "doc_type"),
        # Covers the default list ordering (newest doc_date first).
        Index("ix_mgmt_documents_owner_doc_date", "owner_id", text("doc_date DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_documents_owner_id"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Free text, NOT an enum — the corpus vocabulary is open.
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    # User-asserted document date (board-meeting date, memo date) —
    # distinct from created_at (row insert time).
    doc_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comma-separated storyline/topic tags, free text.
    related_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Inline markdown body — v1 design decision (see module docstring).
    content_md: Mapped[str] = mapped_column(Text, nullable=False)

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
            f"<MgmtDocument id={self.id} owner_id={self.owner_id} "
            f"title={self.title!r} doc_type={self.doc_type!r} "
            f"deleted={self.deleted_at is not None}>"
        )
