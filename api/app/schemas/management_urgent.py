"""Pydantic schemas for the Urgent Matters surface (Management tab).

Wire shape for ``GET /api/v1/management/urgent-matters`` — a computed,
read-only feed with no tables of its own. The handler
(:mod:`app.api.management_urgent`) scans three sources — open
stakeholder commitments, stakeholder cadence breaches, and KPIs at or
below the red attainment band — and buckets each finding into ``red``
(same-day attention) or ``yellow`` (this-week attention).

Every item carries the same shape regardless of ``kind`` so the UI can
render one list component; source-specific fields (``due_date``,
``stakeholder_*``, ``kpi_id``) are null when they don't apply.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel

UrgentKind = Literal["commitment", "cadence", "kpi"]
"""Which source produced the item."""


class UrgentItem(BaseModel):
    """One urgent-matters row (red or yellow bucket).

    * ``title`` — short display line. Commitments prefix the trimmed
      description with the direction ("Deliver: ..." for ``we_owe``,
      "Chase: ..." for ``they_owe``); individual-scope KPI items are
      titled with the team member's name.
    * ``why`` — one factual line explaining the flag (due date and
      distance, cadence gap, or attainment percentage).
    * ``next_step`` — one imperative line derived from ``kind``.
    * ``days_until_due`` — negative means overdue. Null for cadence and
      KPI items (no due date).
    * ``link`` — frontend route: commitments and cadence breaches point
      at the stakeholder dossier, KPI items at the KPI detail page.
    """

    kind: UrgentKind
    title: str
    why: str
    next_step: str
    due_date: datetime.date | None = None
    days_until_due: int | None = None
    stakeholder_id: uuid.UUID | None = None
    stakeholder_name: str | None = None
    kpi_id: uuid.UUID | None = None
    link: str


class UrgentMattersRead(BaseModel):
    """``GET /api/v1/management/urgent-matters`` response.

    ``red`` = act today; ``yellow`` = act this week. The combined list
    is capped at 10 items, reds surviving the cap first. Ordering
    encodes the triage judgment: reds by ``days_until_due`` ascending
    (most-overdue first, then soonest due); yellows as commitments (by
    due date), then cadence breaches (largest gap first), then KPIs
    (worst attainment first).
    """

    generated_at: datetime.datetime
    red: list[UrgentItem]
    yellow: list[UrgentItem]
