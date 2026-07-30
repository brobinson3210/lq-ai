"""management_ai_jobs — Management tab AI-features module table

Adds one table:
- mgmt_ai_jobs (owner-scoped background AI-draft jobs: pre_meeting_brief
  / review_prep / kpi_draft; subject pairing enforced by a table CHECK
  — pre_meeting_brief iff stakeholder_id, review_prep iff
  team_member_id; status pending → running → done | error; result_md
  for briefs, result_json for validated KPI drafts; subject FKs SET
  NULL so job history survives subject deletion; (owner_id,
  created_at DESC) index for the newest-first list)

Revision ID: 0069
Revises: 0068
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mgmt_ai_jobs",
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
                name="fk_mgmt_ai_jobs_owner_id",
            ),
            nullable=False,
        ),
        sa.Column("job_type", sa.Text(), nullable=False),
        # Subject FKs — SET NULL so deleting the subject keeps the job
        # row as history without a dangling reference.
        sa.Column(
            "stakeholder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "stakeholders.id",
                ondelete="SET NULL",
                name="fk_mgmt_ai_jobs_stakeholder_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "team_member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mgmt_team_members.id",
                ondelete="SET NULL",
                name="fk_mgmt_ai_jobs_team_member_id",
            ),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        # Wizard answers etc. — request-side inputs, kept for transparency.
        sa.Column("params", postgresql.JSONB(), nullable=True),
        # The brief (markdown) for pre_meeting_brief / review_prep jobs.
        sa.Column("result_md", sa.Text(), nullable=True),
        # Validated KPI-draft catalog for kpi_draft jobs.
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "job_type IN ('pre_meeting_brief', 'review_prep', 'kpi_draft')",
            name="chk_mgmt_ai_jobs_job_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'error')",
            name="chk_mgmt_ai_jobs_status",
        ),
        # Structural subject pairing: pre_meeting_brief iff stakeholder,
        # review_prep iff team member; kpi_draft has neither.
        sa.CheckConstraint(
            "(job_type = 'pre_meeting_brief') = (stakeholder_id IS NOT NULL) "
            "AND (job_type = 'review_prep') = (team_member_id IS NOT NULL)",
            name="chk_mgmt_ai_jobs_subject",
        ),
    )
    # Covers the newest-first list endpoint per owner.
    op.create_index(
        "ix_mgmt_ai_jobs_owner_created",
        "mgmt_ai_jobs",
        ["owner_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_mgmt_ai_jobs_owner_created", table_name="mgmt_ai_jobs")
    op.drop_table("mgmt_ai_jobs")
