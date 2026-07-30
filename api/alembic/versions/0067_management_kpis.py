"""management_kpis — Management tab KPIs module tables

Adds three tables:
- mgmt_team_members  (owner-scoped legal/compliance team roster; soft delete
  via deleted_at; name-length + department CHECKs)
- mgmt_kpis          (owner-scoped KPI definitions; scope department|individual
  with a table-level CHECK pairing scope='individual' with a non-null
  team_member_id; cadence + direction CHECKs; (owner_id, department) index)
- mgmt_kpi_datapoints (one measured value per KPI per period; period is
  'YYYY-MM' or 'YYYY-Qn' text validated in the API layer; UNIQUE
  (kpi_id, period) — its backing index also covers the period range scans)

Revision ID: 0067
Revises: 0066
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create mgmt_team_members
    # ------------------------------------------------------------------
    op.create_table(
        "mgmt_team_members",
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
                name="fk_mgmt_team_members_owner_id",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role_title", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("seniority", sa.Text(), nullable=True),
        sa.Column("strengths_md", sa.Text(), nullable=True),
        sa.Column("development_areas_md", sa.Text(), nullable=True),
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
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_team_members_name_len",
        ),
        sa.CheckConstraint(
            "department IN ('legal', 'compliance')",
            name="chk_mgmt_team_members_department",
        ),
    )
    op.create_index(
        "ix_mgmt_team_members_owner_id",
        "mgmt_team_members",
        ["owner_id"],
    )

    # ------------------------------------------------------------------
    # 2. Create mgmt_kpis
    # ------------------------------------------------------------------
    op.create_table(
        "mgmt_kpis",
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
                name="fk_mgmt_kpis_owner_id",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column(
            "team_member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mgmt_team_members.id",
                ondelete="CASCADE",
                name="fk_mgmt_kpis_team_member_id",
            ),
            nullable=True,
        ),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("cadence", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("baseline", sa.Numeric(), nullable=True),
        sa.Column("target", sa.Numeric(), nullable=True),
        sa.Column("rationale_md", sa.Text(), nullable=True),
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
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_kpis_name_len",
        ),
        sa.CheckConstraint(
            "department IN ('legal', 'compliance')",
            name="chk_mgmt_kpis_department",
        ),
        sa.CheckConstraint(
            "scope IN ('department', 'individual')",
            name="chk_mgmt_kpis_scope",
        ),
        sa.CheckConstraint(
            "(scope = 'individual') = (team_member_id IS NOT NULL)",
            name="chk_mgmt_kpis_scope_team_member",
        ),
        sa.CheckConstraint(
            "cadence IN ('monthly', 'quarterly')",
            name="chk_mgmt_kpis_cadence",
        ),
        sa.CheckConstraint(
            "direction IN ('higher_is_better', 'lower_is_better')",
            name="chk_mgmt_kpis_direction",
        ),
    )
    op.create_index(
        "ix_mgmt_kpis_owner_department",
        "mgmt_kpis",
        ["owner_id", "department"],
    )

    # ------------------------------------------------------------------
    # 3. Create mgmt_kpi_datapoints
    # ------------------------------------------------------------------
    op.create_table(
        "mgmt_kpi_datapoints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "kpi_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mgmt_kpis.id",
                ondelete="CASCADE",
                name="fk_mgmt_kpi_datapoints_kpi_id",
            ),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("note_md", sa.Text(), nullable=True),
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
        # The unique constraint's backing index also covers the
        # (kpi_id, period) range scans — no separate index needed.
        sa.UniqueConstraint("kpi_id", "period", name="uq_mgmt_kpi_datapoints_kpi_period"),
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 3. Drop mgmt_kpi_datapoints
    # ------------------------------------------------------------------
    op.drop_table("mgmt_kpi_datapoints")

    # ------------------------------------------------------------------
    # 2. Drop mgmt_kpis
    # ------------------------------------------------------------------
    op.drop_index("ix_mgmt_kpis_owner_department", table_name="mgmt_kpis")
    op.drop_table("mgmt_kpis")

    # ------------------------------------------------------------------
    # 1. Drop mgmt_team_members
    # ------------------------------------------------------------------
    op.drop_index("ix_mgmt_team_members_owner_id", table_name="mgmt_team_members")
    op.drop_table("mgmt_team_members")
