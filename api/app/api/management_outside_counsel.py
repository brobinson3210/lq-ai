"""Outside Counsel endpoints — Management tab, Outside Counsel module.

Surface (all under ``/api/v1/management/outside-counsel``):

* ``POST/GET /firms``                 — the caller's panel of outside firms,
  each with its chosen partners and computed rate flags.
* ``GET/PATCH/DELETE /firms/{id}``    — single firm (DELETE soft, 204).
* ``POST /firms/{id}/partners``       — record a partner the GC chose.
* ``PATCH/DELETE /partners/{id}``     — update a partner; ``status=
  'left_firm'`` raises the red alert, ``'replaced'`` / ``'followed'``
  resolves it.
* ``PUT/GET /budgets``                — upsert / list phased quarterly
  budgets (department total ``all`` or one practice area).
* ``DELETE /budgets/{id}``            — remove one budget row (hard).
* ``POST/GET /invoices``              — line-item invoices; filters
  ``?firm_id=``, ``?period=``, ``?year=``.
* ``GET/PATCH/DELETE /invoices/{id}`` — single invoice; a PATCH carrying
  ``lines`` replaces them wholesale. DELETE soft, 204.
* ``POST/GET /value-entries``         — the value ledger (method note and
  source mandatory).
* ``PATCH/DELETE /value-entries/{id}``
* ``GET /summary``                    — the module dashboard for one year.

**GC policy (round-4 interview, 2026-09-30).** Firms must give at least
a 10% discount (below is RED); hourly increases above 5% are YELLOW;
quarter spend at 110%+ of budget is YELLOW and 125%+ is RED; no more
than two people may bill the same task on the same day (one partner and
one associate). The thresholds below are the single source of truth —
:mod:`app.api.management_urgent` imports them.

**Staffing flags** are computed per invoice: lines are grouped by work
date and normalised task text (case, punctuation and spacing ignored);
a group with more than :data:`MAX_BILLERS_PER_TASK` distinct timekeepers
is flagged.

**Per-user isolation.** Owner-scoped exactly as the sibling Management
modules: cross-user access returns 404. Partners and invoice lines are
scoped through their parent rows. Every mutation writes an
``audit_log`` row in the same transaction.
"""

from __future__ import annotations

import datetime as dt_mod
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.api.dependencies import ActiveUser
from app.audit import audit_action
from app.db.session import get_db
from app.errors import NotFound, ValidationError
from app.models.management_document import MgmtDocument
from app.models.management_outside_counsel import (
    OC_VALUE_CATEGORIES,
    MgmtOcBudget,
    MgmtOcFirm,
    MgmtOcFirmPartner,
    MgmtOcInvoice,
    MgmtOcInvoiceLine,
    MgmtOcValueEntry,
)
from app.models.stakeholder import Stakeholder
from app.schemas.management_outside_counsel import (
    MgmtOcAmountRow,
    MgmtOcBudgetRead,
    MgmtOcBudgetUpsert,
    MgmtOcFirmCreate,
    MgmtOcFirmRead,
    MgmtOcFirmUpdate,
    MgmtOcInvoiceCreate,
    MgmtOcInvoiceLineIn,
    MgmtOcInvoiceLineRead,
    MgmtOcInvoiceRead,
    MgmtOcInvoiceUpdate,
    MgmtOcPartnerAlert,
    MgmtOcPartnerCreate,
    MgmtOcPartnerRead,
    MgmtOcPartnerUpdate,
    MgmtOcQuarterRow,
    MgmtOcRateFlag,
    MgmtOcStaffingFlag,
    MgmtOcSummaryRead,
    MgmtOcValueEntryCreate,
    MgmtOcValueEntryRead,
    MgmtOcValueEntryUpdate,
)

router = APIRouter(prefix="/management/outside-counsel", tags=["management-outside-counsel"])
log = logging.getLogger(__name__)

# --- GC policy thresholds (see module docstring) ---------------------------
MIN_DISCOUNT_PCT = Decimal(10)
MAX_RATE_INCREASE_PCT = Decimal(5)
BUDGET_YELLOW_PCT = Decimal(110)
BUDGET_RED_PCT = Decimal(125)
MAX_BILLERS_PER_TASK = 2

_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")
_YEAR_RE = re.compile(r"^\d{4}$")
_PERIOD_RE = re.compile(r"^\d{4}-Q[1-4]$")
_TASK_STRIP_RE = re.compile(r"[^a-z0-9 ]+")

_AREA_LABELS: dict[str, str] = {
    "all": "Department total",
    "commercial": "Commercial",
    "corporate": "Corporate / M&A",
    "employment": "Employment",
    "ip": "IP",
    "litigation": "Litigation",
    "privacy": "Privacy",
    "regulatory": "Regulatory",
    "other": "Other",
}
CATEGORY_LABELS: dict[str, str] = {
    "self_service_savings": "Self-service savings",
    "billing_adjustments": "Billing adjustments & rate discipline",
    "insourcing_avoidance": "Insourcing avoidance",
    "settlement_avoidance": "Settlement / demand avoidance",
}


# ---------------------------------------------------------------------------
# Pure helpers (shared with Urgent Matters and the AI spend story)
# ---------------------------------------------------------------------------


def money(value: Decimal | None) -> str:
    """Decimal → cents string (the wire format for money)."""

    return str((value or Decimal(0)).quantize(_CENT, rounding=ROUND_HALF_UP))


def pct_str(value: Decimal) -> str:
    return str(value.quantize(_TENTH, rounding=ROUND_HALF_UP))


def period_for(day: dt_mod.date) -> str:
    """Calendar quarter key ``YYYY-Qn`` for a date."""

    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def previous_period(period: str) -> str:
    year, q = int(period[:4]), int(period[-1])
    return f"{year - 1}-Q4" if q == 1 else f"{year}-Q{q - 1}"


def budget_band(pct: Decimal | None) -> str | None:
    """Traffic light for spend as a % of budget (GC policy)."""

    if pct is None:
        return None
    if pct >= BUDGET_RED_PCT:
        return "red"
    if pct >= BUDGET_YELLOW_PCT:
        return "yellow"
    return "green"


def pct_of(actual: Decimal, budget: Decimal | None) -> Decimal | None:
    if budget is None or budget <= 0:
        return None
    return (actual / budget * 100).quantize(_TENTH, rounding=ROUND_HALF_UP)


def normalise_task(task: str) -> str:
    """Case/punctuation/spacing-insensitive key for the staffing rule."""

    return " ".join(_TASK_STRIP_RE.sub(" ", task.lower()).split())


def staffing_flags(
    invoice: MgmtOcInvoice,
    firm_name: str,
    lines: Iterable[MgmtOcInvoiceLine],
) -> list[MgmtOcStaffingFlag]:
    """Flag (work_date, task) groups billed by too many distinct people."""

    groups: dict[tuple[dt_mod.date, str], list[MgmtOcInvoiceLine]] = defaultdict(list)
    for line in lines:
        groups[(line.work_date, normalise_task(line.task))].append(line)

    flags: list[MgmtOcStaffingFlag] = []
    for (work_date, _), group in sorted(groups.items(), key=lambda kv: kv[0]):
        people = sorted({ln.timekeeper.strip() for ln in group}, key=str.lower)
        if len(people) <= MAX_BILLERS_PER_TASK:
            continue
        flags.append(
            MgmtOcStaffingFlag(
                invoice_id=invoice.id,
                invoice_number=invoice.invoice_number,
                firm_id=invoice.firm_id,
                firm_name=firm_name,
                work_date=work_date,
                task=group[0].task,
                timekeepers=people,
                amount=money(sum((ln.amount for ln in group), Decimal(0))),
            )
        )
    return flags


def firm_flags(firm: MgmtOcFirm) -> tuple[bool, bool]:
    """(below the discount floor, above the increase cap)."""

    below = firm.discount_pct is not None and firm.discount_pct < MIN_DISCOUNT_PCT
    above = firm.rate_increase_pct is not None and firm.rate_increase_pct > MAX_RATE_INCREASE_PCT
    return below, above


async def quarter_actuals(
    db: AsyncSession, owner_id: uuid.UUID, periods: Sequence[str]
) -> dict[str, Decimal]:
    """Invoice spend per period (active invoices of active firms)."""

    stmt = (
        select(MgmtOcInvoice.period, func.coalesce(func.sum(MgmtOcInvoiceLine.amount), 0))
        .join(MgmtOcInvoiceLine, MgmtOcInvoiceLine.invoice_id == MgmtOcInvoice.id)
        .join(MgmtOcFirm, MgmtOcFirm.id == MgmtOcInvoice.firm_id)
        .where(
            MgmtOcInvoice.owner_id == owner_id,
            MgmtOcInvoice.deleted_at.is_(None),
            MgmtOcFirm.deleted_at.is_(None),
            MgmtOcInvoice.period.in_(list(periods)),
        )
        .group_by(MgmtOcInvoice.period)
    )
    return {p: Decimal(v) for p, v in (await db.execute(stmt)).all()}


async def quarter_budgets(
    db: AsyncSession, owner_id: uuid.UUID, periods: Sequence[str]
) -> dict[str, Decimal]:
    """Department budget per period: the ``all`` row, else the sum of areas."""

    rows = (
        await db.execute(
            select(MgmtOcBudget.period, MgmtOcBudget.practice_area, MgmtOcBudget.amount).where(
                MgmtOcBudget.owner_id == owner_id,
                MgmtOcBudget.period.in_(list(periods)),
            )
        )
    ).all()
    totals: dict[str, Decimal] = {}
    area_sums: dict[str, Decimal] = defaultdict(Decimal)
    for period, area, amount in rows:
        if area == "all":
            totals[period] = Decimal(amount)
        else:
            area_sums[period] += Decimal(amount)
    for period, amount in area_sums.items():
        totals.setdefault(period, amount)
    return totals


# ---------------------------------------------------------------------------
# Loaders and read builders
# ---------------------------------------------------------------------------


def _validate_id(value: str, *, param: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(f"{param} must be a UUID", details={param: value}) from exc


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _load_firm(db: AsyncSession, firm_id: uuid.UUID, owner_id: uuid.UUID) -> MgmtOcFirm:
    row = (
        await db.execute(
            select(MgmtOcFirm).where(
                MgmtOcFirm.id == firm_id,
                MgmtOcFirm.owner_id == owner_id,
                MgmtOcFirm.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"Firm {firm_id} not found.", details={"firm_id": str(firm_id)})
    return row


async def _load_partner(
    db: AsyncSession, partner_id: uuid.UUID, owner_id: uuid.UUID
) -> MgmtOcFirmPartner:
    row = (
        await db.execute(
            select(MgmtOcFirmPartner)
            .join(MgmtOcFirm, MgmtOcFirm.id == MgmtOcFirmPartner.firm_id)
            .where(
                MgmtOcFirmPartner.id == partner_id,
                MgmtOcFirmPartner.deleted_at.is_(None),
                MgmtOcFirm.owner_id == owner_id,
                MgmtOcFirm.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"Partner {partner_id} not found.", details={"partner_id": str(partner_id)})
    return row


async def _load_invoice(
    db: AsyncSession, invoice_id: uuid.UUID, owner_id: uuid.UUID
) -> MgmtOcInvoice:
    row = (
        await db.execute(
            select(MgmtOcInvoice).where(
                MgmtOcInvoice.id == invoice_id,
                MgmtOcInvoice.owner_id == owner_id,
                MgmtOcInvoice.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"Invoice {invoice_id} not found.", details={"invoice_id": str(invoice_id)})
    return row


async def _load_value_entry(
    db: AsyncSession, entry_id: uuid.UUID, owner_id: uuid.UUID
) -> MgmtOcValueEntry:
    row = (
        await db.execute(
            select(MgmtOcValueEntry).where(
                MgmtOcValueEntry.id == entry_id,
                MgmtOcValueEntry.owner_id == owner_id,
                MgmtOcValueEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(
            f"Value entry {entry_id} not found.", details={"value_entry_id": str(entry_id)}
        )
    return row


async def _check_stakeholder(
    db: AsyncSession, stakeholder_id: uuid.UUID, owner_id: uuid.UUID
) -> None:
    found = (
        await db.execute(
            select(Stakeholder.id).where(
                Stakeholder.id == stakeholder_id,
                Stakeholder.owner_id == owner_id,
                Stakeholder.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise NotFound(
            f"Stakeholder {stakeholder_id} not found.",
            details={"stakeholder_id": str(stakeholder_id)},
        )


async def _check_document(db: AsyncSession, document_id: uuid.UUID, owner_id: uuid.UUID) -> None:
    found = (
        await db.execute(
            select(MgmtDocument.id).where(
                MgmtDocument.id == document_id,
                MgmtDocument.owner_id == owner_id,
                MgmtDocument.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise NotFound(
            f"Document {document_id} not found.", details={"document_id": str(document_id)}
        )


def _partner_read(p: MgmtOcFirmPartner) -> MgmtOcPartnerRead:
    return MgmtOcPartnerRead.model_validate(p)


async def _firm_reads(
    db: AsyncSession, owner_id: uuid.UUID, firm_ids: Sequence[uuid.UUID] | None = None
) -> list[MgmtOcFirmRead]:
    """Firms (optionally restricted) with partners and spend, three queries."""

    conds: list[ColumnElement[bool]] = [
        MgmtOcFirm.owner_id == owner_id,
        MgmtOcFirm.deleted_at.is_(None),
    ]
    if firm_ids is not None:
        conds.append(MgmtOcFirm.id.in_(list(firm_ids)))
    firms = (
        (await db.execute(select(MgmtOcFirm).where(*conds).order_by(MgmtOcFirm.name)))
        .scalars()
        .all()
    )
    if not firms:
        return []
    ids = [f.id for f in firms]

    partners = (
        (
            await db.execute(
                select(MgmtOcFirmPartner)
                .where(
                    MgmtOcFirmPartner.firm_id.in_(ids),
                    MgmtOcFirmPartner.deleted_at.is_(None),
                )
                .order_by(MgmtOcFirmPartner.created_at)
            )
        )
        .scalars()
        .all()
    )
    by_firm: dict[uuid.UUID, list[MgmtOcPartnerRead]] = defaultdict(list)
    for p in partners:
        by_firm[p.firm_id].append(_partner_read(p))

    spend_rows = (
        await db.execute(
            select(
                MgmtOcInvoice.firm_id,
                func.coalesce(func.sum(MgmtOcInvoiceLine.amount), 0),
                func.count(func.distinct(MgmtOcInvoice.id)),
            )
            .join(MgmtOcInvoiceLine, MgmtOcInvoiceLine.invoice_id == MgmtOcInvoice.id)
            .where(MgmtOcInvoice.firm_id.in_(ids), MgmtOcInvoice.deleted_at.is_(None))
            .group_by(MgmtOcInvoice.firm_id)
        )
    ).all()
    spend = {fid: (Decimal(total), int(count)) for fid, total, count in spend_rows}

    out: list[MgmtOcFirmRead] = []
    for f in firms:
        below, above = firm_flags(f)
        total, count = spend.get(f.id, (Decimal(0), 0))
        out.append(
            MgmtOcFirmRead(
                id=f.id,
                owner_id=f.owner_id,
                name=f.name,
                discount_pct=str(f.discount_pct) if f.discount_pct is not None else None,
                rate_increase_pct=(
                    str(f.rate_increase_pct) if f.rate_increase_pct is not None else None
                ),
                rate_year=f.rate_year,
                notes_md=f.notes_md,
                created_at=f.created_at,
                updated_at=f.updated_at,
                partners=by_firm.get(f.id, []),
                below_discount_floor=below,
                above_increase_cap=above,
                spend_total=money(total),
                invoice_count=count,
            )
        )
    return out


async def _one_firm_read(
    db: AsyncSession, owner_id: uuid.UUID, firm_id: uuid.UUID
) -> MgmtOcFirmRead:
    reads = await _firm_reads(db, owner_id, [firm_id])
    if not reads:
        raise NotFound(f"Firm {firm_id} not found.", details={"firm_id": str(firm_id)})
    return reads[0]


def _line_read(line: MgmtOcInvoiceLine) -> MgmtOcInvoiceLineRead:
    return MgmtOcInvoiceLineRead(
        id=line.id,
        work_date=line.work_date,
        timekeeper=line.timekeeper,
        title=line.title,
        task=line.task,
        hours=str(line.hours),
        rate=money(line.rate),
        amount=money(line.amount),
    )


async def _invoice_reads(
    db: AsyncSession, invoices: Sequence[MgmtOcInvoice]
) -> list[MgmtOcInvoiceRead]:
    if not invoices:
        return []
    ids = [i.id for i in invoices]
    name_rows = (
        await db.execute(
            select(MgmtOcFirm.id, MgmtOcFirm.name).where(
                MgmtOcFirm.id.in_({i.firm_id for i in invoices})
            )
        )
    ).all()
    firm_names: dict[uuid.UUID, str] = {row[0]: row[1] for row in name_rows}
    lines = (
        (
            await db.execute(
                select(MgmtOcInvoiceLine)
                .where(MgmtOcInvoiceLine.invoice_id.in_(ids))
                .order_by(MgmtOcInvoiceLine.work_date, MgmtOcInvoiceLine.timekeeper)
            )
        )
        .scalars()
        .all()
    )
    by_invoice: dict[uuid.UUID, list[MgmtOcInvoiceLine]] = defaultdict(list)
    for ln in lines:
        by_invoice[ln.invoice_id].append(ln)

    out: list[MgmtOcInvoiceRead] = []
    for inv in invoices:
        inv_lines = by_invoice.get(inv.id, [])
        firm_name = firm_names.get(inv.firm_id, "")
        out.append(
            MgmtOcInvoiceRead(
                id=inv.id,
                firm_id=inv.firm_id,
                firm_name=firm_name,
                invoice_number=inv.invoice_number,
                invoice_date=inv.invoice_date,
                period=inv.period,
                practice_area=inv.practice_area,
                matter_ref=inv.matter_ref,
                status=inv.status,
                notes_md=inv.notes_md,
                total=money(sum((ln.amount for ln in inv_lines), Decimal(0))),
                line_count=len(inv_lines),
                lines=[_line_read(ln) for ln in inv_lines],
                staffing_flags=staffing_flags(inv, firm_name, inv_lines),
                created_at=inv.created_at,
                updated_at=inv.updated_at,
            )
        )
    return out


def _new_lines(
    invoice_id: uuid.UUID, lines: Sequence[MgmtOcInvoiceLineIn]
) -> list[MgmtOcInvoiceLine]:
    return [
        MgmtOcInvoiceLine(
            invoice_id=invoice_id,
            work_date=ln.work_date,
            timekeeper=ln.timekeeper,
            title=ln.title,
            task=ln.task,
            hours=ln.hours,
            rate=ln.rate,
            amount=(ln.hours * ln.rate).quantize(_CENT, rounding=ROUND_HALF_UP),
        )
        for ln in lines
    ]


def _budget_read(b: MgmtOcBudget) -> MgmtOcBudgetRead:
    return MgmtOcBudgetRead(
        id=b.id,
        period=b.period,
        practice_area=b.practice_area,
        amount=money(b.amount),
        notes_md=b.notes_md,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


def _value_read(v: MgmtOcValueEntry) -> MgmtOcValueEntryRead:
    return MgmtOcValueEntryRead(
        id=v.id,
        period=v.period,
        category=v.category,
        amount=money(v.amount),
        description=v.description,
        method_note=v.method_note,
        source=v.source,
        document_id=v.document_id,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


def _apply(row: Any, fields: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for field, value in fields.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed.append(field)
    return changed


def _year_or_default(year: str | None) -> int:
    if year is None:
        return _now().year
    if not _YEAR_RE.match(year):
        raise ValidationError("year must be YYYY", details={"year": year})
    return int(year)


# ---------------------------------------------------------------------------
# Firms
# ---------------------------------------------------------------------------


@router.post(
    "/firms",
    response_model=MgmtOcFirmRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add an outside firm",
)
async def create_firm(
    payload: MgmtOcFirmCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcFirmRead:
    firm = MgmtOcFirm(owner_id=user.id, **payload.model_dump())
    db.add(firm)
    await db.flush()
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_firm.create",
        resource_type="mgmt_oc_firm",
        resource_id=str(firm.id),
        request=request,
        details={"name": firm.name},
    )
    await db.commit()
    return await _one_firm_read(db, user.id, firm.id)


@router.get(
    "/firms",
    response_model=list[MgmtOcFirmRead],
    summary="List the caller's outside firms with partners and rate flags",
)
async def list_firms(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MgmtOcFirmRead]:
    return await _firm_reads(db, user.id)


@router.get(
    "/firms/{firm_id}",
    response_model=MgmtOcFirmRead,
    summary="Fetch one firm",
    responses={404: {"description": "Firm not found"}},
)
async def get_firm(
    firm_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcFirmRead:
    fid = _validate_id(firm_id, param="firm_id")
    await _load_firm(db, fid, user.id)
    return await _one_firm_read(db, user.id, fid)


@router.patch(
    "/firms/{firm_id}",
    response_model=MgmtOcFirmRead,
    summary="Update a firm (name, discount, rate increase, notes)",
    responses={404: {"description": "Firm not found"}},
)
async def update_firm(
    firm_id: str,
    payload: MgmtOcFirmUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcFirmRead:
    fid = _validate_id(firm_id, param="firm_id")
    firm = await _load_firm(db, fid, user.id)
    changed = _apply(firm, payload.model_dump(exclude_unset=True))
    if changed:
        firm.updated_at = _now()
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_oc_firm.update",
            resource_type="mgmt_oc_firm",
            resource_id=str(fid),
            request=request,
            details={"changed_fields": sorted(changed)},
        )
        await db.commit()
    return await _one_firm_read(db, user.id, fid)


@router.delete(
    "/firms/{firm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a firm (its invoices drop out of every total)",
    responses={404: {"description": "Firm not found"}},
)
async def delete_firm(
    firm_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    fid = _validate_id(firm_id, param="firm_id")
    firm = await _load_firm(db, fid, user.id)
    firm.deleted_at = _now()
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_firm.delete",
        resource_type="mgmt_oc_firm",
        resource_id=str(fid),
        request=request,
        details={"name": firm.name},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Partners
# ---------------------------------------------------------------------------


@router.post(
    "/firms/{firm_id}/partners",
    response_model=MgmtOcPartnerRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a partner the GC chose at this firm",
    responses={404: {"description": "Firm or stakeholder not found"}},
)
async def create_partner(
    firm_id: str,
    payload: MgmtOcPartnerCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcPartnerRead:
    fid = _validate_id(firm_id, param="firm_id")
    await _load_firm(db, fid, user.id)
    if payload.stakeholder_id is not None:
        await _check_stakeholder(db, payload.stakeholder_id, user.id)
    partner = MgmtOcFirmPartner(firm_id=fid, **payload.model_dump())
    db.add(partner)
    await db.flush()
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_partner.create",
        resource_type="mgmt_oc_partner",
        resource_id=str(partner.id),
        request=request,
        details={"name": partner.name, "firm_id": str(fid)},
    )
    await db.commit()
    await db.refresh(partner)
    return _partner_read(partner)


@router.patch(
    "/partners/{partner_id}",
    response_model=MgmtOcPartnerRead,
    summary="Update a partner — status='left_firm' raises a red alert",
    description=(
        "``status='left_firm'`` stamps ``left_at`` (today unless given) and "
        "puts a RED item on Urgent Matters until the GC resolves it with "
        "``status='replaced'`` (chose a new partner) or ``'followed'`` "
        "(the work follows the partner to the new firm)."
    ),
    responses={404: {"description": "Partner or stakeholder not found"}},
)
async def update_partner(
    partner_id: str,
    payload: MgmtOcPartnerUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcPartnerRead:
    pid = _validate_id(partner_id, param="partner_id")
    partner = await _load_partner(db, pid, user.id)
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("stakeholder_id") is not None:
        await _check_stakeholder(db, fields["stakeholder_id"], user.id)
    if fields.get("status") == "left_firm" and fields.get("left_at") is None:
        fields["left_at"] = partner.left_at or _now().date()
    changed = _apply(partner, fields)
    if changed:
        partner.updated_at = _now()
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_oc_partner.update",
            resource_type="mgmt_oc_partner",
            resource_id=str(pid),
            request=request,
            details={"changed_fields": sorted(changed), "status": partner.status},
        )
        await db.commit()
        await db.refresh(partner)
    return _partner_read(partner)


@router.delete(
    "/partners/{partner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a partner from a firm (soft)",
    responses={404: {"description": "Partner not found"}},
)
async def delete_partner(
    partner_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    pid = _validate_id(partner_id, param="partner_id")
    partner = await _load_partner(db, pid, user.id)
    partner.deleted_at = _now()
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_partner.delete",
        resource_type="mgmt_oc_partner",
        resource_id=str(pid),
        request=request,
        details={"name": partner.name},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


@router.put(
    "/budgets",
    response_model=MgmtOcBudgetRead,
    summary="Set the budget for one quarter (insert or replace)",
)
async def upsert_budget(
    payload: MgmtOcBudgetUpsert,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcBudgetRead:
    existing = (
        await db.execute(
            select(MgmtOcBudget).where(
                MgmtOcBudget.owner_id == user.id,
                MgmtOcBudget.period == payload.period,
                MgmtOcBudget.practice_area == payload.practice_area,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        budget = MgmtOcBudget(owner_id=user.id, **payload.model_dump())
        db.add(budget)
        await db.flush()
        action = "mgmt_oc_budget.create"
    else:
        budget = existing
        _apply(budget, {"amount": payload.amount, "notes_md": payload.notes_md})
        budget.updated_at = _now()
        action = "mgmt_oc_budget.update"
    await audit_action(
        db,
        user_id=user.id,
        action=action,
        resource_type="mgmt_oc_budget",
        resource_id=str(budget.id),
        request=request,
        details={"period": budget.period, "practice_area": budget.practice_area},
    )
    await db.commit()
    await db.refresh(budget)
    return _budget_read(budget)


@router.get(
    "/budgets",
    response_model=list[MgmtOcBudgetRead],
    summary="List budgets, optionally for one year",
)
async def list_budgets(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    year: Annotated[str | None, Query(description="Filter to one year (YYYY).")] = None,
) -> list[MgmtOcBudgetRead]:
    conds: list[ColumnElement[bool]] = [MgmtOcBudget.owner_id == user.id]
    if year is not None:
        y = _year_or_default(year)
        conds.append(MgmtOcBudget.period.like(f"{y}-Q%"))
    rows = (
        (
            await db.execute(
                select(MgmtOcBudget)
                .where(*conds)
                .order_by(MgmtOcBudget.period, MgmtOcBudget.practice_area)
            )
        )
        .scalars()
        .all()
    )
    return [_budget_read(b) for b in rows]


@router.delete(
    "/budgets/{budget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete one budget row",
    responses={404: {"description": "Budget not found"}},
)
async def delete_budget(
    budget_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    bid = _validate_id(budget_id, param="budget_id")
    budget = (
        await db.execute(
            select(MgmtOcBudget).where(MgmtOcBudget.id == bid, MgmtOcBudget.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if budget is None:
        raise NotFound(f"Budget {bid} not found.", details={"budget_id": str(bid)})
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_budget.delete",
        resource_type="mgmt_oc_budget",
        resource_id=str(bid),
        request=request,
        details={"period": budget.period, "practice_area": budget.practice_area},
    )
    await db.delete(budget)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@router.post(
    "/invoices",
    response_model=MgmtOcInvoiceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an invoice, line by line",
    responses={404: {"description": "Firm not found"}},
)
async def create_invoice(
    payload: MgmtOcInvoiceCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcInvoiceRead:
    await _load_firm(db, payload.firm_id, user.id)
    fields = payload.model_dump(exclude={"lines"})
    invoice = MgmtOcInvoice(owner_id=user.id, period=period_for(payload.invoice_date), **fields)
    db.add(invoice)
    await db.flush()
    db.add_all(_new_lines(invoice.id, payload.lines))
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_invoice.create",
        resource_type="mgmt_oc_invoice",
        resource_id=str(invoice.id),
        request=request,
        details={
            "firm_id": str(invoice.firm_id),
            "invoice_number": invoice.invoice_number,
            "lines": len(payload.lines),
        },
    )
    await db.commit()
    await db.refresh(invoice)
    return (await _invoice_reads(db, [invoice]))[0]


@router.get(
    "/invoices",
    response_model=list[MgmtOcInvoiceRead],
    summary="List invoices (newest first) with lines and staffing flags",
)
async def list_invoices(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    firm_id: Annotated[str | None, Query(description="Filter to one firm.")] = None,
    period: Annotated[str | None, Query(description="Filter to one quarter (YYYY-Qn).")] = None,
    year: Annotated[str | None, Query(description="Filter to one year (YYYY).")] = None,
) -> list[MgmtOcInvoiceRead]:
    conds: list[ColumnElement[bool]] = [
        MgmtOcInvoice.owner_id == user.id,
        MgmtOcInvoice.deleted_at.is_(None),
        MgmtOcFirm.deleted_at.is_(None),
    ]
    if firm_id is not None:
        conds.append(MgmtOcInvoice.firm_id == _validate_id(firm_id, param="firm_id"))
    if period is not None:
        if not _PERIOD_RE.match(period):
            raise ValidationError("period must be YYYY-Qn", details={"period": period})
        conds.append(MgmtOcInvoice.period == period)
    if year is not None:
        conds.append(MgmtOcInvoice.period.like(f"{_year_or_default(year)}-Q%"))
    invoices = (
        (
            await db.execute(
                select(MgmtOcInvoice)
                .join(MgmtOcFirm, MgmtOcFirm.id == MgmtOcInvoice.firm_id)
                .where(*conds)
                .order_by(MgmtOcInvoice.invoice_date.desc(), MgmtOcInvoice.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return await _invoice_reads(db, invoices)


@router.get(
    "/invoices/{invoice_id}",
    response_model=MgmtOcInvoiceRead,
    summary="Fetch one invoice",
    responses={404: {"description": "Invoice not found"}},
)
async def get_invoice(
    invoice_id: str,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcInvoiceRead:
    iid = _validate_id(invoice_id, param="invoice_id")
    invoice = await _load_invoice(db, iid, user.id)
    return (await _invoice_reads(db, [invoice]))[0]


@router.patch(
    "/invoices/{invoice_id}",
    response_model=MgmtOcInvoiceRead,
    summary="Update an invoice; ``lines`` replaces all lines",
    responses={404: {"description": "Invoice or firm not found"}},
)
async def update_invoice(
    invoice_id: str,
    payload: MgmtOcInvoiceUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcInvoiceRead:
    iid = _validate_id(invoice_id, param="invoice_id")
    invoice = await _load_invoice(db, iid, user.id)
    fields = payload.model_dump(exclude_unset=True, exclude={"lines"})
    if fields.get("firm_id") is not None:
        await _load_firm(db, fields["firm_id"], user.id)
    for required in ("firm_id", "invoice_date", "practice_area", "status"):
        if required in fields and fields[required] is None:
            raise ValidationError(f"{required} cannot be null", details={"field": required})
    if "invoice_date" in fields:
        fields["period"] = period_for(fields["invoice_date"])
    changed = _apply(invoice, fields)
    if payload.lines is not None:
        await db.execute(delete(MgmtOcInvoiceLine).where(MgmtOcInvoiceLine.invoice_id == iid))
        db.add_all(_new_lines(iid, payload.lines))
        changed.append("lines")
    if changed:
        invoice.updated_at = _now()
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_oc_invoice.update",
            resource_type="mgmt_oc_invoice",
            resource_id=str(iid),
            request=request,
            details={"changed_fields": sorted(changed)},
        )
        await db.commit()
        await db.refresh(invoice)
    return (await _invoice_reads(db, [invoice]))[0]


@router.delete(
    "/invoices/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete an invoice",
    responses={404: {"description": "Invoice not found"}},
)
async def delete_invoice(
    invoice_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    iid = _validate_id(invoice_id, param="invoice_id")
    invoice = await _load_invoice(db, iid, user.id)
    invoice.deleted_at = _now()
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_invoice.delete",
        resource_type="mgmt_oc_invoice",
        resource_id=str(iid),
        request=request,
        details={"invoice_number": invoice.invoice_number},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Value ledger
# ---------------------------------------------------------------------------


@router.post(
    "/value-entries",
    response_model=MgmtOcValueEntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a value-ledger entry (method note and source required)",
    responses={404: {"description": "Document not found"}},
)
async def create_value_entry(
    payload: MgmtOcValueEntryCreate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcValueEntryRead:
    if payload.document_id is not None:
        await _check_document(db, payload.document_id, user.id)
    entry = MgmtOcValueEntry(owner_id=user.id, **payload.model_dump())
    db.add(entry)
    await db.flush()
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_value_entry.create",
        resource_type="mgmt_oc_value_entry",
        resource_id=str(entry.id),
        request=request,
        details={"period": entry.period, "category": entry.category},
    )
    await db.commit()
    await db.refresh(entry)
    return _value_read(entry)


@router.get(
    "/value-entries",
    response_model=list[MgmtOcValueEntryRead],
    summary="List value-ledger entries",
)
async def list_value_entries(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    year: Annotated[str | None, Query(description="Filter to one year (YYYY).")] = None,
    category: Annotated[str | None, Query(description="Filter to one category.")] = None,
) -> list[MgmtOcValueEntryRead]:
    conds: list[ColumnElement[bool]] = [
        MgmtOcValueEntry.owner_id == user.id,
        MgmtOcValueEntry.deleted_at.is_(None),
    ]
    if year is not None:
        conds.append(MgmtOcValueEntry.period.like(f"{_year_or_default(year)}-Q%"))
    if category is not None:
        if category not in OC_VALUE_CATEGORIES:
            raise ValidationError(
                f"Unknown category {category!r}.", details={"allowed": list(OC_VALUE_CATEGORIES)}
            )
        conds.append(MgmtOcValueEntry.category == category)
    rows = (
        (
            await db.execute(
                select(MgmtOcValueEntry)
                .where(*conds)
                .order_by(MgmtOcValueEntry.period.desc(), MgmtOcValueEntry.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_value_read(v) for v in rows]


@router.patch(
    "/value-entries/{entry_id}",
    response_model=MgmtOcValueEntryRead,
    summary="Update a value-ledger entry",
    responses={404: {"description": "Value entry or document not found"}},
)
async def update_value_entry(
    entry_id: str,
    payload: MgmtOcValueEntryUpdate,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MgmtOcValueEntryRead:
    eid = _validate_id(entry_id, param="value_entry_id")
    entry = await _load_value_entry(db, eid, user.id)
    fields = payload.model_dump(exclude_unset=True)
    for required in ("period", "category", "amount", "description", "method_note", "source"):
        if required in fields and fields[required] is None:
            raise ValidationError(f"{required} cannot be null", details={"field": required})
    if fields.get("document_id") is not None:
        await _check_document(db, fields["document_id"], user.id)
    changed = _apply(entry, fields)
    if changed:
        entry.updated_at = _now()
        await audit_action(
            db,
            user_id=user.id,
            action="mgmt_oc_value_entry.update",
            resource_type="mgmt_oc_value_entry",
            resource_id=str(eid),
            request=request,
            details={"changed_fields": sorted(changed)},
        )
        await db.commit()
        await db.refresh(entry)
    return _value_read(entry)


@router.delete(
    "/value-entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Soft-delete a value-ledger entry",
    responses={404: {"description": "Value entry not found"}},
)
async def delete_value_entry(
    entry_id: str,
    request: Request,
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    eid = _validate_id(entry_id, param="value_entry_id")
    entry = await _load_value_entry(db, eid, user.id)
    entry.deleted_at = _now()
    await audit_action(
        db,
        user_id=user.id,
        action="mgmt_oc_value_entry.delete",
        resource_type="mgmt_oc_value_entry",
        resource_id=str(eid),
        request=request,
        details={"period": entry.period, "category": entry.category},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


async def build_summary(db: AsyncSession, owner_id: uuid.UUID, year: int) -> MgmtOcSummaryRead:
    """The whole module dashboard for one year (also feeds the AI memo)."""

    periods = [f"{year}-Q{q}" for q in range(1, 5)]

    invoices = (
        (
            await db.execute(
                select(MgmtOcInvoice)
                .join(MgmtOcFirm, MgmtOcFirm.id == MgmtOcInvoice.firm_id)
                .where(
                    MgmtOcInvoice.owner_id == owner_id,
                    MgmtOcInvoice.deleted_at.is_(None),
                    MgmtOcFirm.deleted_at.is_(None),
                    MgmtOcInvoice.period.in_(periods),
                )
            )
        )
        .scalars()
        .all()
    )
    reads = await _invoice_reads(db, invoices)

    actual_by_q: dict[str, Decimal] = defaultdict(Decimal)
    by_firm: dict[uuid.UUID, tuple[str, Decimal, int]] = {}
    by_area: dict[str, tuple[Decimal, int]] = {}
    flags: list[MgmtOcStaffingFlag] = []
    for r in reads:
        total = Decimal(r.total)
        actual_by_q[r.period] += total
        name, amt, count = by_firm.get(r.firm_id, (r.firm_name, Decimal(0), 0))
        by_firm[r.firm_id] = (name, amt + total, count + 1)
        a_amt, a_count = by_area.get(r.practice_area, (Decimal(0), 0))
        by_area[r.practice_area] = (a_amt + total, a_count + 1)
        flags.extend(r.staffing_flags)

    budget_rows = (
        await db.execute(
            select(MgmtOcBudget.period, MgmtOcBudget.practice_area, MgmtOcBudget.amount).where(
                MgmtOcBudget.owner_id == owner_id, MgmtOcBudget.period.in_(periods)
            )
        )
    ).all()
    area_budget: dict[str, Decimal] = defaultdict(Decimal)
    for _, area, amount in budget_rows:
        if area != "all":
            area_budget[area] += Decimal(amount)
    budgets = await quarter_budgets(db, owner_id, periods)

    quarters: list[MgmtOcQuarterRow] = []
    for p in periods:
        actual = actual_by_q.get(p, Decimal(0))
        budget = budgets.get(p)
        pct = pct_of(actual, budget)
        quarters.append(
            MgmtOcQuarterRow(
                period=p,
                budget=money(budget) if budget is not None else None,
                actual=money(actual),
                pct_of_budget=pct_str(pct) if pct is not None else None,
                band=budget_band(pct) if actual > 0 else None,  # type: ignore[arg-type]
            )
        )
    year_actual = sum(actual_by_q.values(), Decimal(0))
    year_budget = sum(budgets.values(), Decimal(0)) if budgets else None
    year_pct = pct_of(year_actual, year_budget)

    # Rate flags and partner alerts are about the panel now, not the year.
    firms = (
        (
            await db.execute(
                select(MgmtOcFirm)
                .where(MgmtOcFirm.owner_id == owner_id, MgmtOcFirm.deleted_at.is_(None))
                .order_by(MgmtOcFirm.name)
            )
        )
        .scalars()
        .all()
    )
    rate_flags: list[MgmtOcRateFlag] = []
    for f in firms:
        below, above = firm_flags(f)
        if below:
            rate_flags.append(
                MgmtOcRateFlag(
                    firm_id=f.id,
                    firm_name=f.name,
                    issue="discount_below_floor",
                    value=str(f.discount_pct),
                    band="red",
                )
            )
        if above:
            rate_flags.append(
                MgmtOcRateFlag(
                    firm_id=f.id,
                    firm_name=f.name,
                    issue="increase_above_cap",
                    value=str(f.rate_increase_pct),
                    band="yellow",
                )
            )

    alert_rows = (
        await db.execute(
            select(MgmtOcFirmPartner, MgmtOcFirm.name)
            .join(MgmtOcFirm, MgmtOcFirm.id == MgmtOcFirmPartner.firm_id)
            .where(
                MgmtOcFirm.owner_id == owner_id,
                MgmtOcFirm.deleted_at.is_(None),
                MgmtOcFirmPartner.deleted_at.is_(None),
                MgmtOcFirmPartner.status == "left_firm",
            )
            .order_by(MgmtOcFirmPartner.left_at)
        )
    ).all()
    partner_alerts = [
        MgmtOcPartnerAlert(
            partner_id=p.id,
            partner_name=p.name,
            firm_id=p.firm_id,
            firm_name=firm_name,
            left_at=p.left_at,
        )
        for p, firm_name in alert_rows
    ]

    value_rows = (
        await db.execute(
            select(MgmtOcValueEntry.category, func.sum(MgmtOcValueEntry.amount), func.count())
            .where(
                MgmtOcValueEntry.owner_id == owner_id,
                MgmtOcValueEntry.deleted_at.is_(None),
                MgmtOcValueEntry.period.in_(periods),
            )
            .group_by(MgmtOcValueEntry.category)
        )
    ).all()
    value_map = {cat: (Decimal(amt), int(n)) for cat, amt, n in value_rows}
    value_by_category = [
        MgmtOcAmountRow(
            key=cat,
            label=CATEGORY_LABELS[cat],
            amount=money(value_map.get(cat, (Decimal(0), 0))[0]),
            count=value_map.get(cat, (Decimal(0), 0))[1],
        )
        for cat in OC_VALUE_CATEGORIES
    ]

    return MgmtOcSummaryRead(
        year=year,
        min_discount_pct=str(MIN_DISCOUNT_PCT),
        max_rate_increase_pct=str(MAX_RATE_INCREASE_PCT),
        max_billers_per_task=MAX_BILLERS_PER_TASK,
        quarters=quarters,
        year_budget=money(year_budget) if year_budget is not None else None,
        year_actual=money(year_actual),
        year_pct_of_budget=pct_str(year_pct) if year_pct is not None else None,
        by_firm=[
            MgmtOcAmountRow(key=str(fid), label=name, amount=money(amt), count=count)
            for fid, (name, amt, count) in sorted(by_firm.items(), key=lambda kv: -kv[1][1])
        ],
        by_practice_area=[
            MgmtOcAmountRow(
                key=area,
                label=_AREA_LABELS.get(area, area),
                amount=money(amt),
                budget=money(area_budget[area]) if area in area_budget else None,
                count=count,
            )
            for area, (amt, count) in sorted(by_area.items(), key=lambda kv: -kv[1][0])
        ],
        staffing_flags=flags,
        rate_flags=rate_flags,
        partner_alerts=partner_alerts,
        value_total=money(sum((v[0] for v in value_map.values()), Decimal(0))),
        value_by_category=value_by_category,
    )


@router.get(
    "/summary",
    response_model=MgmtOcSummaryRead,
    summary="Outside-counsel dashboard: budget vs actual, firms, flags, value ledger",
    description=(
        "One call for the module page: budget vs actual per quarter of "
        "``year`` (default: this year) with a traffic-light band (110%+ "
        "yellow, 125%+ red), spend by firm and by practice area, staffing "
        "flags, firms below the 10% discount floor or above the 5% "
        "increase cap, partners marked as having left their firm, and "
        "value-ledger totals by category."
    ),
)
async def get_summary(
    user: ActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    year: Annotated[str | None, Query(description="Year (YYYY); default this year.")] = None,
) -> MgmtOcSummaryRead:
    return await build_summary(db, user.id, _year_or_default(year))


__all__ = [
    "BUDGET_RED_PCT",
    "BUDGET_YELLOW_PCT",
    "CATEGORY_LABELS",
    "MAX_BILLERS_PER_TASK",
    "MAX_RATE_INCREASE_PCT",
    "MIN_DISCOUNT_PCT",
    "budget_band",
    "build_summary",
    "firm_flags",
    "money",
    "normalise_task",
    "pct_of",
    "pct_str",
    "period_for",
    "previous_period",
    "quarter_actuals",
    "quarter_budgets",
    "router",
    "staffing_flags",
]
