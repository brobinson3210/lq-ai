"""management_documents — Management tab Documents module table

Adds one table:
- mgmt_documents (owner-scoped private document space for executive work
  product; inline markdown content_md — v1 stores TEXT in Postgres, not
  binary uploads through the ingestion pipeline; doc_type is free text
  CHECK-bounded 1..60, NOT an enum; soft delete via deleted_at;
  (owner_id, doc_type) and (owner_id, doc_date DESC) indexes)

Revision ID: 0068
Revises: 0067
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mgmt_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="RESTRICT",
                name="fk_mgmt_documents_owner_id",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        # Free text, NOT an enum — the corpus vocabulary is open.
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        # Comma-separated storyline/topic tags, free text.
        sa.Column("related_tags", sa.Text(), nullable=True),
        # Inline markdown body — v1 stores text in Postgres; binary/PDF
        # upload via the ingestion pipeline is roadmap.
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(title) > 0 AND char_length(title) <= 300",
            name="chk_mgmt_documents_title_len",
        ),
        sa.CheckConstraint(
            "char_length(doc_type) > 0 AND char_length(doc_type) <= 60",
            name="chk_mgmt_documents_doc_type_len",
        ),
        sa.CheckConstraint(
            "author IS NULL OR char_length(author) <= 200",
            name="chk_mgmt_documents_author_len",
        ),
    )
    op.create_index(
        "ix_mgmt_documents_owner_doc_type",
        "mgmt_documents",
        ["owner_id", "doc_type"],
    )
    op.create_index(
        "ix_mgmt_documents_owner_doc_date",
        "mgmt_documents",
        ["owner_id", sa.text("doc_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_mgmt_documents_owner_doc_date", table_name="mgmt_documents")
    op.drop_index("ix_mgmt_documents_owner_doc_type", table_name="mgmt_documents")
    op.drop_table("mgmt_documents")
