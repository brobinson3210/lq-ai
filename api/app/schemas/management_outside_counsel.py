"""Pydantic schemas for the Outside Counsel surface (Management tab).

Wire shapes for ``/api/v1/management/outside-counsel/*``. The ORM models
live in ``app.models.management_outside_counsel``.

Conventions match ``app.schemas.management_kpis``: write models forbid
extra fields; update models make every field optional and the handlers
apply ``model_dump(exclude_unset=True)``; **numeric fields are JSON
strings on the read side** (Decimal-as-string per CLAUDE.md) while write
models accept a JSON number or string.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NAME_MAX_LEN: int = 200
"""Matches the ``chk_mgmt_oc_*_name_len`` CHECK constraints."""

PracticeArea = Literal[
    "commercial",
    "corporate",
    "employment",
    "ip",
    "litigation",
    "privacy",
    "regulatory",
    "other",
]
BudgetArea = Literal[
    "all",
    "commercial",
    "corporate",
    "employment",
    "ip",
    "litigation",
    "privacy",
    "regulatory",
    "other",
]
TimekeeperTitle = Literal["partner", "counsel", "associate", "paralegal", "other"]
PartnerStatus = Literal["active", "left_firm", "replaced", "followed"]
InvoiceStatus = Literal["received", "paid"]
ValueCategory = Literal[
    "self_service_savings",
    "billing_adjustments",
    "insourcing_avoidance",
    "settlement_avoidance",
]
Band = Literal["green", "yellow", "red"]

OcName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=NAME_MAX_LEN, strip_whitespace=True),
]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
QuarterPeriod = Annotated[str, StringConstraints(pattern=r"^\d{4}-Q[1-4]$")]
Pct = Annotated[Decimal, Field(ge=0, le=100)]
Money = Annotated[Decimal, Field(ge=0)]


# ---------------------------------------------------------------------------
# Partners
# ---------------------------------------------------------------------------


class MgmtOcPartnerCreate(BaseModel):
    """``POST /management/outside-counsel/firms/{firm_id}/partners`` body."""

    model_config = ConfigDict(extra="forbid")

    name: OcName
    stakeholder_id: uuid.UUID | None = None
    practice_area: PracticeArea | None = None


class MgmtOcPartnerUpdate(BaseModel):
    """``PATCH /management/outside-counsel/partners/{id}`` body.

    Marking a partner as gone: ``status='left_firm'`` (``left_at``
    defaults to today). Resolving the alert: ``status='replaced'`` or
    ``'followed'``, ideally with a ``resolution_note``.
    """

    model_config = ConfigDict(extra="forbid")

    name: OcName | None = None
    stakeholder_id: uuid.UUID | None = None
    practice_area: PracticeArea | None = None
    status: PartnerStatus | None = None
    left_at: datetime.date | None = None
    resolution_note: str | None = None


class MgmtOcPartnerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    firm_id: uuid.UUID
    stakeholder_id: uuid.UUID | None = None
    name: str
    practice_area: str | None = None
    status: str
    left_at: datetime.date | None = None
    resolution_note: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------------------
# Firms
# ---------------------------------------------------------------------------


class MgmtOcFirmCreate(BaseModel):
    """``POST /management/outside-counsel/firms`` body."""

    model_config = ConfigDict(extra="forbid")

    name: OcName
    discount_pct: Pct | None = None
    rate_increase_pct: Decimal | None = None
    rate_year: int | None = None
    notes_md: str | None = None


class MgmtOcFirmUpdate(BaseModel):
    """``PATCH /management/outside-counsel/firms/{id}`` body — all optional."""

    model_config = ConfigDict(extra="forbid")

    name: OcName | None = None
    discount_pct: Pct | None = None
    rate_increase_pct: Decimal | None = None
    rate_year: int | None = None
    notes_md: str | None = None


class MgmtOcFirmRead(BaseModel):
    """Firm wire shape — row columns, chosen partners, and computed flags.

    * ``below_discount_floor`` — ``discount_pct`` under the 10% minimum.
    * ``above_increase_cap`` — ``rate_increase_pct`` over the 5% cap.
    * ``spend_total`` / ``invoice_count`` — across all active invoices.
    """

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    discount_pct: str | None = None
    rate_increase_pct: str | None = None
    rate_year: int | None = None
    notes_md: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    partners: list[MgmtOcPartnerRead] = []
    below_discount_floor: bool = False
    above_increase_cap: bool = False
    spend_total: str = "0"
    invoice_count: int = 0


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


class MgmtOcBudgetUpsert(BaseModel):
    """``PUT /management/outside-counsel/budgets`` body — insert or replace.

    One row per (period, practice_area); ``practice_area='all'`` is the
    department-total line.
    """

    model_config = ConfigDict(extra="forbid")

    period: QuarterPeriod
    practice_area: BudgetArea = "all"
    amount: Money
    notes_md: str | None = None


class MgmtOcBudgetRead(BaseModel):
    id: uuid.UUID
    period: str
    practice_area: str
    amount: str
    notes_md: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


class MgmtOcInvoiceLineIn(BaseModel):
    """One time entry. ``amount`` is computed (hours x rate), never sent."""

    model_config = ConfigDict(extra="forbid")

    work_date: datetime.date
    timekeeper: OcName
    title: TimekeeperTitle
    task: NonEmptyText
    hours: Money
    rate: Money


class MgmtOcInvoiceLineRead(BaseModel):
    id: uuid.UUID
    work_date: datetime.date
    timekeeper: str
    title: str
    task: str
    hours: str
    rate: str
    amount: str


class MgmtOcStaffingFlag(BaseModel):
    """More than the allowed number of people billed one task on one day."""

    invoice_id: uuid.UUID
    invoice_number: str | None = None
    firm_id: uuid.UUID
    firm_name: str
    work_date: datetime.date
    task: str
    timekeepers: list[str]
    amount: str


class MgmtOcInvoiceCreate(BaseModel):
    """``POST /management/outside-counsel/invoices`` body (line by line)."""

    model_config = ConfigDict(extra="forbid")

    firm_id: uuid.UUID
    invoice_number: str | None = None
    invoice_date: datetime.date
    practice_area: PracticeArea
    matter_ref: str | None = None
    status: InvoiceStatus = "received"
    notes_md: str | None = None
    lines: list[MgmtOcInvoiceLineIn] = Field(min_length=1)


class MgmtOcInvoiceUpdate(BaseModel):
    """``PATCH /management/outside-counsel/invoices/{id}`` body.

    ``lines``, when present, REPLACES the invoice's lines wholesale.
    """

    model_config = ConfigDict(extra="forbid")

    firm_id: uuid.UUID | None = None
    invoice_number: str | None = None
    invoice_date: datetime.date | None = None
    practice_area: PracticeArea | None = None
    matter_ref: str | None = None
    status: InvoiceStatus | None = None
    notes_md: str | None = None
    lines: list[MgmtOcInvoiceLineIn] | None = Field(default=None, min_length=1)


class MgmtOcInvoiceRead(BaseModel):
    id: uuid.UUID
    firm_id: uuid.UUID
    firm_name: str
    invoice_number: str | None = None
    invoice_date: datetime.date
    period: str
    practice_area: str
    matter_ref: str | None = None
    status: str
    notes_md: str | None = None
    total: str
    line_count: int
    lines: list[MgmtOcInvoiceLineRead] = []
    staffing_flags: list[MgmtOcStaffingFlag] = []
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------------------
# Value ledger
# ---------------------------------------------------------------------------


class MgmtOcValueEntryCreate(BaseModel):
    """``POST /management/outside-counsel/value-entries`` body.

    ``method_note`` (how the number was calculated) and ``source`` (the
    record or document it traces to) are mandatory — nothing reaches the
    board without its receipt.
    """

    model_config = ConfigDict(extra="forbid")

    period: QuarterPeriod
    category: ValueCategory
    amount: Money
    description: NonEmptyText
    method_note: NonEmptyText
    source: NonEmptyText
    document_id: uuid.UUID | None = None


class MgmtOcValueEntryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: QuarterPeriod | None = None
    category: ValueCategory | None = None
    amount: Money | None = None
    description: NonEmptyText | None = None
    method_note: NonEmptyText | None = None
    source: NonEmptyText | None = None
    document_id: uuid.UUID | None = None


class MgmtOcValueEntryRead(BaseModel):
    id: uuid.UUID
    period: str
    category: str
    amount: str
    description: str
    method_note: str
    source: str
    document_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class MgmtOcQuarterRow(BaseModel):
    """Budget vs actual for one quarter. ``pct_of_budget`` null = no budget."""

    period: str
    budget: str | None = None
    actual: str
    pct_of_budget: str | None = None
    band: Band | None = None


class MgmtOcAmountRow(BaseModel):
    key: str
    label: str
    amount: str
    budget: str | None = None
    count: int = 0


class MgmtOcRateFlag(BaseModel):
    firm_id: uuid.UUID
    firm_name: str
    issue: Literal["discount_below_floor", "increase_above_cap"]
    value: str
    band: Band


class MgmtOcPartnerAlert(BaseModel):
    partner_id: uuid.UUID
    partner_name: str
    firm_id: uuid.UUID
    firm_name: str
    left_at: datetime.date | None = None


class MgmtOcSummaryRead(BaseModel):
    """``GET /management/outside-counsel/summary`` — the module dashboard."""

    year: int
    min_discount_pct: str
    max_rate_increase_pct: str
    max_billers_per_task: int
    quarters: list[MgmtOcQuarterRow]
    year_budget: str | None = None
    year_actual: str
    year_pct_of_budget: str | None = None
    by_firm: list[MgmtOcAmountRow]
    by_practice_area: list[MgmtOcAmountRow]
    staffing_flags: list[MgmtOcStaffingFlag]
    rate_flags: list[MgmtOcRateFlag]
    partner_alerts: list[MgmtOcPartnerAlert]
    value_total: str
    value_by_category: list[MgmtOcAmountRow]
