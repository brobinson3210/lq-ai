"""outside_counsel — Management tab Outside Counsel module tables

Adds six owner-scoped tables (see ``app.models.management_outside_counsel``):

- mgmt_oc_firms          (outside firms; discount_pct + rate_increase_pct)
- mgmt_oc_firm_partners  (the partners the GC chose; left_firm alert state)
- mgmt_oc_budgets        (phased quarterly budget, total or per practice area)
- mgmt_oc_invoices       (invoice headers; total = sum of lines)
- mgmt_oc_invoice_lines  (line-item time entries)
- mgmt_oc_value_entries  (value ledger; method_note + source mandatory)

Also widens ``chk_mgmt_ai_jobs_job_type`` to admit ``spend_story`` (the
CFO-language spend memo). spend_story has no subject id, so the existing
subject-pairing CHECK holds unchanged.

Revision ID: 0071
Revises: 0070
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRACTICE_AREAS = (
    "'commercial', 'corporate', 'employment', 'ip', 'litigation', 'privacy', 'regulatory', 'other'"
)


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _owner(table: str) -> sa.Column:
    return sa.Column(
        "owner_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT", name=f"fk_{table}_owner_id"),
        nullable=False,
    )


def _ts(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _deleted() -> sa.Column:
    return sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True)


def upgrade() -> None:
    # 1. Firms
    op.create_table(
        "mgmt_oc_firms",
        _id(),
        _owner("mgmt_oc_firms"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("discount_pct", sa.Numeric(), nullable=True),
        sa.Column("rate_increase_pct", sa.Numeric(), nullable=True),
        sa.Column("rate_year", sa.Integer(), nullable=True),
        sa.Column("notes_md", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _deleted(),
        sa.CheckConstraint(
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_oc_firms_name_len",
        ),
        sa.CheckConstraint(
            "discount_pct IS NULL OR (discount_pct >= 0 AND discount_pct <= 100)",
            name="chk_mgmt_oc_firms_discount_range",
        ),
    )
    op.create_index("ix_mgmt_oc_firms_owner_id", "mgmt_oc_firms", ["owner_id"])

    # 2. Chosen partners
    op.create_table(
        "mgmt_oc_firm_partners",
        _id(),
        sa.Column(
            "firm_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mgmt_oc_firms.id", ondelete="CASCADE", name="fk_mgmt_oc_firm_partners_firm_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "stakeholder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "stakeholders.id",
                ondelete="SET NULL",
                name="fk_mgmt_oc_firm_partners_stakeholder_id",
            ),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("practice_area", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("left_at", sa.Date(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _deleted(),
        sa.CheckConstraint(
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_oc_firm_partners_name_len",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'left_firm', 'replaced', 'followed')",
            name="chk_mgmt_oc_firm_partners_status",
        ),
        sa.CheckConstraint(
            f"practice_area IS NULL OR practice_area IN ({_PRACTICE_AREAS})",
            name="chk_mgmt_oc_firm_partners_practice_area",
        ),
    )
    op.create_index("ix_mgmt_oc_firm_partners_firm_id", "mgmt_oc_firm_partners", ["firm_id"])

    # 3. Budgets
    op.create_table(
        "mgmt_oc_budgets",
        _id(),
        _owner("mgmt_oc_budgets"),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("practice_area", sa.Text(), server_default=sa.text("'all'"), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("notes_md", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(
            f"practice_area IN ('all', {_PRACTICE_AREAS})",
            name="chk_mgmt_oc_budgets_practice_area",
        ),
        sa.CheckConstraint("amount >= 0", name="chk_mgmt_oc_budgets_amount"),
        sa.UniqueConstraint(
            "owner_id", "period", "practice_area", name="uq_mgmt_oc_budgets_owner_period_area"
        ),
    )

    # 4. Invoices
    op.create_table(
        "mgmt_oc_invoices",
        _id(),
        _owner("mgmt_oc_invoices"),
        sa.Column(
            "firm_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mgmt_oc_firms.id", ondelete="CASCADE", name="fk_mgmt_oc_invoices_firm_id"
            ),
            nullable=False,
        ),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("practice_area", sa.Text(), nullable=False),
        sa.Column("matter_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'received'"), nullable=False),
        sa.Column("notes_md", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _deleted(),
        sa.CheckConstraint(
            "status IN ('received', 'paid')",
            name="chk_mgmt_oc_invoices_status",
        ),
        sa.CheckConstraint(
            f"practice_area IN ({_PRACTICE_AREAS})",
            name="chk_mgmt_oc_invoices_practice_area",
        ),
    )
    op.create_index("ix_mgmt_oc_invoices_owner_period", "mgmt_oc_invoices", ["owner_id", "period"])
    op.create_index("ix_mgmt_oc_invoices_firm_id", "mgmt_oc_invoices", ["firm_id"])

    # 5. Invoice lines
    op.create_table(
        "mgmt_oc_invoice_lines",
        _id(),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mgmt_oc_invoices.id",
                ondelete="CASCADE",
                name="fk_mgmt_oc_invoice_lines_invoice_id",
            ),
            nullable=False,
        ),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("timekeeper", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("hours", sa.Numeric(), nullable=False),
        sa.Column("rate", sa.Numeric(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        _ts("created_at"),
        sa.CheckConstraint(
            "title IN ('partner', 'counsel', 'associate', 'paralegal', 'other')",
            name="chk_mgmt_oc_invoice_lines_title",
        ),
        sa.CheckConstraint("hours >= 0 AND rate >= 0", name="chk_mgmt_oc_invoice_lines_nonneg"),
    )
    op.create_index("ix_mgmt_oc_invoice_lines_invoice_id", "mgmt_oc_invoice_lines", ["invoice_id"])

    # 6. Value ledger
    op.create_table(
        "mgmt_oc_value_entries",
        _id(),
        _owner("mgmt_oc_value_entries"),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("method_note", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mgmt_documents.id",
                ondelete="SET NULL",
                name="fk_mgmt_oc_value_entries_document_id",
            ),
            nullable=True,
        ),
        _ts("created_at"),
        _ts("updated_at"),
        _deleted(),
        sa.CheckConstraint(
            "category IN ('self_service_savings', 'billing_adjustments', "
            "'insourcing_avoidance', 'settlement_avoidance')",
            name="chk_mgmt_oc_value_entries_category",
        ),
        sa.CheckConstraint(
            "char_length(method_note) > 0 AND char_length(source) > 0",
            name="chk_mgmt_oc_value_entries_receipt",
        ),
    )
    op.create_index(
        "ix_mgmt_oc_value_entries_owner_period",
        "mgmt_oc_value_entries",
        ["owner_id", "period"],
    )

    # 7. AI job type: spend_story
    op.drop_constraint("chk_mgmt_ai_jobs_job_type", "mgmt_ai_jobs", type_="check")
    op.create_check_constraint(
        "chk_mgmt_ai_jobs_job_type",
        "mgmt_ai_jobs",
        "job_type IN ('pre_meeting_brief', 'review_prep', 'kpi_draft', 'spend_story')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM mgmt_ai_jobs WHERE job_type = 'spend_story'")
    op.drop_constraint("chk_mgmt_ai_jobs_job_type", "mgmt_ai_jobs", type_="check")
    op.create_check_constraint(
        "chk_mgmt_ai_jobs_job_type",
        "mgmt_ai_jobs",
        "job_type IN ('pre_meeting_brief', 'review_prep', 'kpi_draft')",
    )

    op.drop_index("ix_mgmt_oc_value_entries_owner_period", table_name="mgmt_oc_value_entries")
    op.drop_table("mgmt_oc_value_entries")
    op.drop_index("ix_mgmt_oc_invoice_lines_invoice_id", table_name="mgmt_oc_invoice_lines")
    op.drop_table("mgmt_oc_invoice_lines")
    op.drop_index("ix_mgmt_oc_invoices_firm_id", table_name="mgmt_oc_invoices")
    op.drop_index("ix_mgmt_oc_invoices_owner_period", table_name="mgmt_oc_invoices")
    op.drop_table("mgmt_oc_invoices")
    op.drop_table("mgmt_oc_budgets")
    op.drop_index("ix_mgmt_oc_firm_partners_firm_id", table_name="mgmt_oc_firm_partners")
    op.drop_table("mgmt_oc_firm_partners")
    op.drop_index("ix_mgmt_oc_firms_owner_id", table_name="mgmt_oc_firms")
    op.drop_table("mgmt_oc_firms")
