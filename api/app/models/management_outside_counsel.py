"""Outside-Counsel ORM models — Management tab, Outside Counsel module.

Per-user (owner-scoped) outside-counsel spend tracking: "trackable spend
— by quarter, by year, by firm", plus the value ledger that tells the
"legal pays for itself" story. Six tables (migration
``0071_outside_counsel.py``):

* :class:`MgmtOcFirm` — one outside law firm, the way Finance and
  Procurement track it. Carries the two rate terms the GC manages in v1:
  the negotiated ``discount_pct`` and this year's hourly
  ``rate_increase_pct`` (no per-timekeeper rate cards — GC decision,
  round 4).
* :class:`MgmtOcFirmPartner` — the partner(s) the GC actually chose at
  that firm ("I hire partners, not firms"). Optionally linked to the
  relationship dossier in ``stakeholders``. ``status='left_firm'`` is
  the alert the GC asked for; it stays open until resolved as
  ``replaced`` (new partner chosen) or ``followed`` (work follows the
  partner to the new firm).
* :class:`MgmtOcBudget` — the phased budget for one quarter, either the
  department total (``practice_area='all'``) or one practice area.
* :class:`MgmtOcInvoice` / :class:`MgmtOcInvoiceLine` — invoices entered
  line by line (timekeeper, date, task, hours, rate). The invoice total
  is always the sum of its lines — never stored — so the two can never
  disagree. ``period`` (``YYYY-Qn``) is derived from ``invoice_date`` in
  the API layer.
* :class:`MgmtOcValueEntry` — one value-ledger entry. ``method_note`` and
  ``source`` are mandatory: nothing reaches the board without its
  receipt (GC decision, round 4 — Director Osei's question).

Firms, partners, invoices and value entries soft-delete via
``deleted_at``; budgets and invoice lines are hard rows (a budget is
replaced in place; lines are replaced wholesale with their invoice).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

OC_PRACTICE_AREAS: tuple[str, ...] = (
    "commercial",
    "corporate",
    "employment",
    "ip",
    "litigation",
    "privacy",
    "regulatory",
    "other",
)
"""Canonical practice areas for invoices, partners and budgets."""

OC_BUDGET_AREAS: tuple[str, ...] = ("all", *OC_PRACTICE_AREAS)
"""Budget rows also admit ``all`` — the department-total line."""

OC_TIMEKEEPER_TITLES: tuple[str, ...] = ("partner", "counsel", "associate", "paralegal", "other")
"""Canonical ``mgmt_oc_invoice_lines.title`` values."""

OC_PARTNER_STATUSES: tuple[str, ...] = ("active", "left_firm", "replaced", "followed")
"""``left_firm`` is the open alert; ``replaced`` / ``followed`` resolve it."""

OC_INVOICE_STATUSES: tuple[str, ...] = ("received", "paid")
"""Canonical ``mgmt_oc_invoices.status`` values."""

OC_VALUE_CATEGORIES: tuple[str, ...] = (
    "self_service_savings",
    "billing_adjustments",
    "insourcing_avoidance",
    "settlement_avoidance",
)
"""The four value-ledger categories confirmed by the GC (round 4)."""


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """Render ``column IN ('a', 'b', ...)`` for a CHECK constraint."""

    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class MgmtOcFirm(Base):
    """One outside law firm on the caller's panel."""

    __tablename__ = "mgmt_oc_firms"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_oc_firms_name_len",
        ),
        CheckConstraint(
            "discount_pct IS NULL OR (discount_pct >= 0 AND discount_pct <= 100)",
            name="chk_mgmt_oc_firms_discount_range",
        ),
        Index("ix_mgmt_oc_firms_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_oc_firms_owner_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    discount_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    rate_increase_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    rate_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes_md: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<MgmtOcFirm id={self.id} name={self.name!r} deleted={self.deleted_at is not None}>"


class MgmtOcFirmPartner(Base):
    """A partner the GC chose at a firm — scoped through the parent firm."""

    __tablename__ = "mgmt_oc_firm_partners"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) > 0 AND char_length(name) <= 200",
            name="chk_mgmt_oc_firm_partners_name_len",
        ),
        CheckConstraint(
            _in_clause("status", OC_PARTNER_STATUSES),
            name="chk_mgmt_oc_firm_partners_status",
        ),
        CheckConstraint(
            "practice_area IS NULL OR " + _in_clause("practice_area", OC_PRACTICE_AREAS),
            name="chk_mgmt_oc_firm_partners_practice_area",
        ),
        Index("ix_mgmt_oc_firm_partners_firm_id", "firm_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    firm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mgmt_oc_firms.id", ondelete="CASCADE", name="fk_mgmt_oc_firm_partners_firm_id"),
        nullable=False,
    )
    stakeholder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stakeholders.id",
            ondelete="SET NULL",
            name="fk_mgmt_oc_firm_partners_stakeholder_id",
        ),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    practice_area: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    left_at: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<MgmtOcFirmPartner id={self.id} name={self.name!r} status={self.status!r}>"


class MgmtOcBudget(Base):
    """The phased outside-counsel budget for one quarter and area."""

    __tablename__ = "mgmt_oc_budgets"
    __table_args__ = (
        CheckConstraint(
            _in_clause("practice_area", OC_BUDGET_AREAS),
            name="chk_mgmt_oc_budgets_practice_area",
        ),
        CheckConstraint("amount >= 0", name="chk_mgmt_oc_budgets_amount"),
        UniqueConstraint(
            "owner_id", "period", "practice_area", name="uq_mgmt_oc_budgets_owner_period_area"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_oc_budgets_owner_id"),
        nullable=False,
    )
    period: Mapped[str] = mapped_column(Text, nullable=False)
    practice_area: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'all'"))
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    notes_md: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return f"<MgmtOcBudget period={self.period!r} area={self.practice_area!r}>"


class MgmtOcInvoice(Base):
    """One firm invoice; its total is the sum of its lines."""

    __tablename__ = "mgmt_oc_invoices"
    __table_args__ = (
        CheckConstraint(
            _in_clause("status", OC_INVOICE_STATUSES),
            name="chk_mgmt_oc_invoices_status",
        ),
        CheckConstraint(
            _in_clause("practice_area", OC_PRACTICE_AREAS),
            name="chk_mgmt_oc_invoices_practice_area",
        ),
        Index("ix_mgmt_oc_invoices_owner_period", "owner_id", "period"),
        Index("ix_mgmt_oc_invoices_firm_id", "firm_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_oc_invoices_owner_id"),
        nullable=False,
    )
    firm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mgmt_oc_firms.id", ondelete="CASCADE", name="fk_mgmt_oc_invoices_firm_id"),
        nullable=False,
    )
    invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    practice_area: Mapped[str] = mapped_column(Text, nullable=False)
    matter_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'received'"))
    notes_md: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<MgmtOcInvoice id={self.id} number={self.invoice_number!r} period={self.period!r}>"


class MgmtOcInvoiceLine(Base):
    """One time entry on an invoice. ``amount`` = hours x rate, set by the API."""

    __tablename__ = "mgmt_oc_invoice_lines"
    __table_args__ = (
        CheckConstraint(
            _in_clause("title", OC_TIMEKEEPER_TITLES),
            name="chk_mgmt_oc_invoice_lines_title",
        ),
        CheckConstraint("hours >= 0 AND rate >= 0", name="chk_mgmt_oc_invoice_lines_nonneg"),
        Index("ix_mgmt_oc_invoice_lines_invoice_id", "invoice_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "mgmt_oc_invoices.id", ondelete="CASCADE", name="fk_mgmt_oc_invoice_lines_invoice_id"
        ),
        nullable=False,
    )
    work_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    timekeeper: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    hours: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    rate: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return f"<MgmtOcInvoiceLine invoice_id={self.invoice_id} timekeeper={self.timekeeper!r}>"


class MgmtOcValueEntry(Base):
    """One value-ledger entry — always with its method and its receipt."""

    __tablename__ = "mgmt_oc_value_entries"
    __table_args__ = (
        CheckConstraint(
            _in_clause("category", OC_VALUE_CATEGORIES),
            name="chk_mgmt_oc_value_entries_category",
        ),
        CheckConstraint(
            "char_length(method_note) > 0 AND char_length(source) > 0",
            name="chk_mgmt_oc_value_entries_receipt",
        ),
        Index("ix_mgmt_oc_value_entries_owner_period", "owner_id", "period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_mgmt_oc_value_entries_owner_id"),
        nullable=False,
    )
    period: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    method_note: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "mgmt_documents.id",
            ondelete="SET NULL",
            name="fk_mgmt_oc_value_entries_document_id",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<MgmtOcValueEntry period={self.period!r} category={self.category!r}>"
