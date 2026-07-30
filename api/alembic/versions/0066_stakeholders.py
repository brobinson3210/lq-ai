"""stakeholders — Management tab Stakeholders module tables

Adds four tables:
- stakeholders               (owner-scoped header row per person; soft delete
  via deleted_at; stakeholder_type / overall_health / cadence CHECKs)
- stakeholder_interactions   (touchpoint log; (stakeholder_id, occurred_at DESC)
  index covers the last-interaction subquery)
- stakeholder_commitments    (open-loop items in either direction; status
  lifecycle open -> done | dropped; (stakeholder_id, status) index)
- stakeholder_positions      (per-topic stance history; latest as_of wins for
  display; (stakeholder_id, topic, as_of DESC) index)

Revision ID: 0066
Revises: 0065
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create stakeholders
    # ------------------------------------------------------------------
    op.create_table(
        "stakeholders",
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
                name="fk_stakeholders_owner_id",
            ),
            nullable=False,
        ),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("role_title", sa.Text(), nullable=True),
        sa.Column("stakeholder_type", sa.Text(), nullable=False),
        sa.Column("committee_seats", sa.Text(), nullable=True),
        sa.Column("overall_health", sa.Text(), nullable=True),
        sa.Column("cadence_target_days", sa.Integer(), nullable=True),
        sa.Column("interests_md", sa.Text(), nullable=True),
        sa.Column("communication_preferences_md", sa.Text(), nullable=True),
        sa.Column("notes_md", sa.Text(), nullable=True),
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
            "char_length(full_name) > 0 AND char_length(full_name) <= 200",
            name="chk_stakeholders_full_name_len",
        ),
        sa.CheckConstraint(
            "stakeholder_type IN ('board_chair', 'director', 'ceo', 'c_suite_peer', "
            "'investor_sponsor', 'lender', 'customer', 'regulator', 'auditor', "
            "'outside_counsel', 'media', 'other')",
            name="chk_stakeholders_type",
        ),
        sa.CheckConstraint(
            "overall_health IS NULL OR overall_health IN "
            "('strong', 'solid', 'needs_attention', 'at_risk')",
            name="chk_stakeholders_health",
        ),
        sa.CheckConstraint(
            "cadence_target_days IS NULL OR cadence_target_days > 0",
            name="chk_stakeholders_cadence_positive",
        ),
    )
    op.create_index(
        "ix_stakeholders_owner_id",
        "stakeholders",
        ["owner_id"],
    )

    # ------------------------------------------------------------------
    # 2. Create stakeholder_interactions
    # ------------------------------------------------------------------
    op.create_table(
        "stakeholder_interactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "stakeholder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "stakeholders.id",
                ondelete="CASCADE",
                name="fk_stakeholder_interactions_stakeholder_id",
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("summary_md", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('meeting', 'call', 'email', 'message', 'board_meeting', "
            "'social', 'other')",
            name="chk_stakeholder_interactions_channel",
        ),
    )
    op.create_index(
        "ix_stakeholder_interactions_stakeholder_occurred",
        "stakeholder_interactions",
        ["stakeholder_id", sa.text("occurred_at DESC")],
    )

    # ------------------------------------------------------------------
    # 3. Create stakeholder_commitments
    # ------------------------------------------------------------------
    op.create_table(
        "stakeholder_commitments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "stakeholder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "stakeholders.id",
                ondelete="CASCADE",
                name="fk_stakeholder_commitments_stakeholder_id",
            ),
            nullable=False,
        ),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
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
        sa.CheckConstraint(
            "direction IN ('we_owe', 'they_owe')",
            name="chk_stakeholder_commitments_direction",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'dropped')",
            name="chk_stakeholder_commitments_status",
        ),
    )
    op.create_index(
        "ix_stakeholder_commitments_stakeholder_status",
        "stakeholder_commitments",
        ["stakeholder_id", "status"],
    )

    # ------------------------------------------------------------------
    # 4. Create stakeholder_positions
    # ------------------------------------------------------------------
    op.create_table(
        "stakeholder_positions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "stakeholder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "stakeholders.id",
                ondelete="CASCADE",
                name="fk_stakeholder_positions_stakeholder_id",
            ),
            nullable=False,
        ),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("stance", sa.Text(), nullable=False),
        sa.Column("note_md", sa.Text(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stance IN ('champion', 'supportive', 'neutral', 'skeptical', 'opposed', 'unknown')",
            name="chk_stakeholder_positions_stance",
        ),
    )
    op.create_index(
        "ix_stakeholder_positions_stakeholder_topic_asof",
        "stakeholder_positions",
        ["stakeholder_id", "topic", sa.text("as_of DESC")],
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 4. Drop stakeholder_positions
    # ------------------------------------------------------------------
    op.drop_index(
        "ix_stakeholder_positions_stakeholder_topic_asof",
        table_name="stakeholder_positions",
    )
    op.drop_table("stakeholder_positions")

    # ------------------------------------------------------------------
    # 3. Drop stakeholder_commitments
    # ------------------------------------------------------------------
    op.drop_index(
        "ix_stakeholder_commitments_stakeholder_status",
        table_name="stakeholder_commitments",
    )
    op.drop_table("stakeholder_commitments")

    # ------------------------------------------------------------------
    # 2. Drop stakeholder_interactions
    # ------------------------------------------------------------------
    op.drop_index(
        "ix_stakeholder_interactions_stakeholder_occurred",
        table_name="stakeholder_interactions",
    )
    op.drop_table("stakeholder_interactions")

    # ------------------------------------------------------------------
    # 1. Drop stakeholders
    # ------------------------------------------------------------------
    op.drop_index("ix_stakeholders_owner_id", table_name="stakeholders")
    op.drop_table("stakeholders")
