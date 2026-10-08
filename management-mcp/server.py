"""Management-tab MCP connector — LQ.AI's Management modules from the terminal.

An MCP (Model Context Protocol) stdio server that lets an MCP client
(Claude Code in a terminal today; potentially an autonomous agent later)
read and edit the entries in the Management tab's modules: stakeholders,
interactions, commitments, positions, team members, KPIs and their
datapoints, documents, outside counsel (firms, chosen partners, budgets,
line-item invoices, the value ledger), plus the read-only Urgent
Matters, KPI dashboard and outside-counsel summary feeds.

Security posture — the fence
----------------------------

This connector is deliberately **limited to the Management tab** and can
never touch anything else in LQ.AI:

* It is a pure API client. It holds no database access and imports
  nothing from the LQ.AI application — every operation goes through the
  same authenticated REST endpoints the browser UI uses, so owner
  scoping, validation, soft deletes, and audit behavior are identical
  to clicking in the UI.
* Every HTTP call is routed through :func:`_request`, which refuses any
  path outside the Management allowlist (``/stakeholders``,
  ``/stakeholder-commitments``, ``/management/``) — defense in depth on
  top of the fact that no tool constructs any other path.
* There is intentionally NO generic "call any endpoint" tool.

Configuration (environment variables, normally set by ``run.sh``):

* ``LQ_MGMT_BASE_URL`` — API base (default ``http://localhost:8000/api/v1``)
* ``LQ_MGMT_EMAIL``    — LQ.AI login email (default ``admin@lq.ai``)
* ``LQ_MGMT_PASSWORD`` — LQ.AI password (``run.sh`` fetches it from the
  macOS Keychain; never stored in a file)

Deletes are LQ.AI soft deletes (recoverable in the database), but the
rows disappear from the UI — the delete tools say so in their
descriptions so a model treats them as destructive.
"""

from __future__ import annotations

import datetime
import os
import sys
from typing import Any
from urllib.parse import unquote

import httpx

try:
    from mcp.server.fastmcp import FastMCP  # mcp SDK 1.x
except ModuleNotFoundError:  # pragma: no cover — SDK 2.x moved FastMCP out
    from fastmcp import FastMCP

BASE_URL = os.environ.get("LQ_MGMT_BASE_URL", "http://localhost:8000/api/v1").rstrip("/")
EMAIL = os.environ.get("LQ_MGMT_EMAIL", "admin@lq.ai")
PASSWORD = os.environ.get("LQ_MGMT_PASSWORD", "")

# The fence: every request path must start with one of these.
_ALLOWED_PREFIXES = ("/stakeholders", "/stakeholder-commitments", "/management/")

mcp = FastMCP("lq-management")

_client = httpx.Client(base_url=BASE_URL, timeout=30.0)
_token: str | None = None


def _login() -> str:
    if not PASSWORD:
        raise RuntimeError(
            "LQ_MGMT_PASSWORD is not set. Launch via run.sh (it reads the "
            "macOS Keychain entry 'lq-ai-management')."
        )
    resp = _client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    resp.raise_for_status()
    return resp.json()["access_token"]


def _check_fence(method: str, path: str) -> None:
    """Refuse any request that would land outside the Management tab.

    Checks both the path as written and the path httpx will actually
    send: an id containing ``../`` (or its percent-encoded form) would
    otherwise pass a naive prefix check and then be normalised by the
    URL layer into a different LQ.AI area.
    """

    blocked = ValueError(
        f"Blocked: {path!r} is outside the Management tab. "
        "This connector only touches Management modules."
    )
    decoded = unquote(path)
    if any(seg in (".", "..") for seg in decoded.split("/")) or "\\" in decoded:
        raise blocked
    if not path.startswith(_ALLOWED_PREFIXES):
        raise blocked
    base_path = httpx.URL(BASE_URL).path.rstrip("/")
    sent = _client.build_request(method, path).url.path
    if not any(sent.startswith(base_path + prefix) for prefix in _ALLOWED_PREFIXES):
        raise blocked


def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Authenticated call to the LQ.AI API — Management paths only."""

    _check_fence(method, path)

    global _token
    if _token is None:
        _token = _login()
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    for attempt in (1, 2):
        resp = _client.request(
            method,
            path,
            json=json,
            params=clean_params,
            headers={"Authorization": f"Bearer {_token}"},
        )
        if resp.status_code == 401 and attempt == 1:
            _token = _login()  # expired token — re-login once and retry
            continue
        break
    if resp.status_code >= 400:
        # Surface the API's validation message to the model so it can fix
        # its inputs (e.g. a bad enum value or period format).
        raise RuntimeError(f"LQ.AI API {resp.status_code} on {method} {path}: {resp.text}")
    if resp.status_code == 204:
        return {"ok": True}
    return resp.json()


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------
# Stakeholders (Relationships zone)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_stakeholders(
    stakeholder_type: str | None = None,
    space: str | None = None,
    needs_attention: bool | None = None,
) -> Any:
    """List stakeholders in the relationship registry.

    Optional filters: stakeholder_type (board_chair | director | ceo |
    c_suite_peer | investor_sponsor | lender | customer | regulator |
    auditor | outside_counsel | media | other), space (a named type
    group, e.g. 'board'), needs_attention=true (only stakeholders past
    their contact cadence). Each row includes overall_health
    (green/yellow/red), days_since_last_interaction, and
    open_commitments_count.
    """
    return _request(
        "GET",
        "/stakeholders",
        params={
            "stakeholder_type": stakeholder_type,
            "space": space,
            "needs_attention": needs_attention,
        },
    )


@mcp.tool()
def get_stakeholder(stakeholder_id: str) -> Any:
    """Fetch one stakeholder's full record by id (UUID)."""
    return _request("GET", f"/stakeholders/{stakeholder_id}")


@mcp.tool()
def create_stakeholder(
    full_name: str,
    stakeholder_type: str,
    organization: str | None = None,
    role_title: str | None = None,
    committee_seats: str | None = None,
    overall_health: str | None = None,
    cadence_target_days: int | None = None,
    interests_md: str | None = None,
    communication_preferences_md: str | None = None,
    notes_md: str | None = None,
) -> Any:
    """Add a stakeholder to the registry.

    stakeholder_type must be one of: board_chair, director, ceo,
    c_suite_peer, investor_sponsor, lender, customer, regulator,
    auditor, outside_counsel, media, other. overall_health, if given,
    is green | yellow | red. cadence_target_days is the contact-cadence
    target in days (positive integer).
    """
    return _request(
        "POST",
        "/stakeholders",
        json=_drop_none(
            {
                "full_name": full_name,
                "stakeholder_type": stakeholder_type,
                "organization": organization,
                "role_title": role_title,
                "committee_seats": committee_seats,
                "overall_health": overall_health,
                "cadence_target_days": cadence_target_days,
                "interests_md": interests_md,
                "communication_preferences_md": communication_preferences_md,
                "notes_md": notes_md,
            }
        ),
    )


@mcp.tool()
def update_stakeholder(stakeholder_id: str, updates: dict[str, Any]) -> Any:
    """Edit a stakeholder (partial update — only the fields in `updates` change).

    Allowed keys: full_name, organization, role_title, stakeholder_type,
    committee_seats, overall_health (green|yellow|red), cadence_target_days,
    interests_md, communication_preferences_md, notes_md. Pass null as a
    value to clear a nullable field.
    """
    return _request("PATCH", f"/stakeholders/{stakeholder_id}", json=updates)


@mcp.tool()
def delete_stakeholder(stakeholder_id: str) -> Any:
    """DESTRUCTIVE: remove a stakeholder from all screens (soft delete —
    recoverable in the database, but it disappears from the UI)."""
    return _request("DELETE", f"/stakeholders/{stakeholder_id}")


# -- Interactions ------------------------------------------------------------


@mcp.tool()
def log_interaction(
    stakeholder_id: str,
    summary_md: str,
    channel: str = "other",
    occurred_at: str | None = None,
) -> Any:
    """Log a touchpoint with a stakeholder (resets their cadence clock).

    channel: meeting | call | email | message | board_meeting | social |
    other. occurred_at is an ISO datetime; defaults to now (UTC).
    """
    if occurred_at is None:
        occurred_at = datetime.datetime.now(tz=datetime.UTC).isoformat()
    return _request(
        "POST",
        f"/stakeholders/{stakeholder_id}/interactions",
        json={"occurred_at": occurred_at, "channel": channel, "summary_md": summary_md},
    )


@mcp.tool()
def list_interactions(stakeholder_id: str) -> Any:
    """List a stakeholder's logged interactions, newest first."""
    return _request("GET", f"/stakeholders/{stakeholder_id}/interactions")


# -- Commitments -------------------------------------------------------------


@mcp.tool()
def add_commitment(
    stakeholder_id: str,
    direction: str,
    description: str,
    due_date: str | None = None,
    status: str = "open",
) -> Any:
    """Record a commitment with a stakeholder.

    direction: we_owe (the GC owes it) | they_owe (they owe the GC).
    due_date: YYYY-MM-DD (optional). status: open | done | dropped
    (defaults to open).
    """
    return _request(
        "POST",
        f"/stakeholders/{stakeholder_id}/commitments",
        json=_drop_none(
            {
                "direction": direction,
                "description": description,
                "due_date": due_date,
                "status": status,
            }
        ),
    )


@mcp.tool()
def list_commitments(
    stakeholder_id: str | None = None,
    status: str | None = None,
    direction: str | None = None,
) -> Any:
    """List commitments — one stakeholder's (pass stakeholder_id) or all
    stakeholders' (omit it; rows then include full_name and
    stakeholder_type). Filters: status (open|done|dropped), direction
    (we_owe|they_owe; all-stakeholders list only)."""
    if stakeholder_id is not None:
        return _request(
            "GET",
            f"/stakeholders/{stakeholder_id}/commitments",
            params={"status": status},
        )
    return _request(
        "GET", "/stakeholder-commitments", params={"status": status, "direction": direction}
    )


@mcp.tool()
def update_commitment(commitment_id: str, updates: dict[str, Any]) -> Any:
    """Edit a commitment (partial update). Allowed keys: direction
    (we_owe|they_owe), description, due_date (YYYY-MM-DD or null),
    status (open|done|dropped). Marking one done = {"status": "done"}."""
    return _request("PATCH", f"/stakeholder-commitments/{commitment_id}", json=updates)


# -- Positions ---------------------------------------------------------------


@mcp.tool()
def add_position(
    stakeholder_id: str,
    topic: str,
    stance: str,
    as_of: str | None = None,
    note_md: str | None = None,
) -> Any:
    """Record a stakeholder's stance on a topic (append-only history —
    to change a stance, add a new row with a later as_of date).

    stance: champion | supportive | neutral | skeptical | opposed |
    unknown. as_of: YYYY-MM-DD, defaults to today.
    """
    if as_of is None:
        as_of = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
    return _request(
        "POST",
        f"/stakeholders/{stakeholder_id}/positions",
        json=_drop_none({"topic": topic, "stance": stance, "as_of": as_of, "note_md": note_md}),
    )


@mcp.tool()
def list_positions(stakeholder_id: str, latest_only: bool = True) -> Any:
    """List a stakeholder's positions. latest_only=true (default) returns
    only the current stance per topic; false returns full history."""
    return _request(
        "GET",
        f"/stakeholders/{stakeholder_id}/positions",
        params={"latest": latest_only},
    )


# ---------------------------------------------------------------------------
# Team + KPIs (Operations zone)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_team_members() -> Any:
    """List the legal/compliance team roster (with each member's KPI count)."""
    return _request("GET", "/management/team-members")


@mcp.tool()
def create_team_member(
    name: str,
    role_title: str,
    department: str,
    seniority: str | None = None,
    strengths_md: str | None = None,
    development_areas_md: str | None = None,
    notes_md: str | None = None,
) -> Any:
    """Add a team member. department: legal | compliance."""
    return _request(
        "POST",
        "/management/team-members",
        json=_drop_none(
            {
                "name": name,
                "role_title": role_title,
                "department": department,
                "seniority": seniority,
                "strengths_md": strengths_md,
                "development_areas_md": development_areas_md,
                "notes_md": notes_md,
            }
        ),
    )


@mcp.tool()
def update_team_member(team_member_id: str, updates: dict[str, Any]) -> Any:
    """Edit a team member (partial update). Allowed keys: name, role_title,
    department (legal|compliance), seniority, strengths_md,
    development_areas_md, notes_md."""
    return _request("PATCH", f"/management/team-members/{team_member_id}", json=updates)


@mcp.tool()
def delete_team_member(team_member_id: str) -> Any:
    """DESTRUCTIVE: remove a team member (soft delete — disappears from the UI)."""
    return _request("DELETE", f"/management/team-members/{team_member_id}")


@mcp.tool()
def list_kpis(
    department: str | None = None,
    scope: str | None = None,
    team_member_id: str | None = None,
) -> Any:
    """List KPI trackers with latest value and attainment_pct (% of target;
    >100 always means beating target). Filters: department
    (legal|compliance), scope (department|individual), team_member_id."""
    return _request(
        "GET",
        "/management/kpis",
        params={"department": department, "scope": scope, "team_member_id": team_member_id},
    )


@mcp.tool()
def get_kpi(kpi_id: str) -> Any:
    """Fetch one KPI tracker with its computed latest/attainment fields."""
    return _request("GET", f"/management/kpis/{kpi_id}")


@mcp.tool()
def get_kpi_series(kpi_id: str, from_period: str | None = None, to_period: str | None = None) -> Any:
    """Fetch a KPI plus its full datapoint history (the chart feed).
    Optional inclusive period bounds, e.g. from_period='2025-Q1'."""
    return _request(
        "GET",
        f"/management/kpis/{kpi_id}/series",
        params={"from": from_period, "to": to_period},
    )


@mcp.tool()
def create_kpi(
    name: str,
    department: str,
    scope: str,
    unit: str,
    cadence: str,
    direction: str,
    target: float | str | None = None,
    baseline: float | str | None = None,
    team_member_id: str | None = None,
    rationale_md: str | None = None,
) -> Any:
    """Create a KPI tracker.

    department: legal | compliance. scope: department | individual
    (individual requires team_member_id). cadence: monthly | quarterly
    (drives datapoint period format YYYY-MM vs YYYY-Qn). direction:
    higher_is_better | lower_is_better.
    """
    return _request(
        "POST",
        "/management/kpis",
        json=_drop_none(
            {
                "name": name,
                "department": department,
                "scope": scope,
                "unit": unit,
                "cadence": cadence,
                "direction": direction,
                "target": target,
                "baseline": baseline,
                "team_member_id": team_member_id,
                "rationale_md": rationale_md,
            }
        ),
    )


@mcp.tool()
def update_kpi(kpi_id: str, updates: dict[str, Any]) -> Any:
    """Edit a KPI tracker (partial update). Allowed keys: name, department,
    scope, team_member_id, unit, cadence, direction, baseline, target,
    rationale_md. (scope='individual' must pair with a team_member_id.)"""
    return _request("PATCH", f"/management/kpis/{kpi_id}", json=updates)


@mcp.tool()
def delete_kpi(kpi_id: str) -> Any:
    """DESTRUCTIVE: remove a KPI tracker and its chart from the UI (soft delete)."""
    return _request("DELETE", f"/management/kpis/{kpi_id}")


@mcp.tool()
def record_kpi_datapoint(
    kpi_id: str,
    period: str,
    value: float | str,
    note_md: str | None = None,
    overwrite: bool = False,
) -> Any:
    """Record a KPI measurement. period format follows the KPI's cadence:
    'YYYY-MM' for monthly, 'YYYY-Qn' for quarterly. If the period already
    has a value, this fails unless overwrite=true (which updates it)."""
    return _request(
        "POST",
        f"/management/kpis/{kpi_id}/datapoints",
        json=_drop_none({"period": period, "value": value, "note_md": note_md}),
        params={"overwrite": overwrite} if overwrite else None,
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@mcp.tool()
def list_documents(
    doc_type: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> Any:
    """List Management documents (metadata only — bodies are fetched with
    get_document). Filters: doc_type (exact match, open vocabulary like
    'board pack', 'memo'), search (case-insensitive over title/author/tags,
    not the body), date_from/date_to (inclusive doc_date bounds,
    YYYY-MM-DD)."""
    return _request(
        "GET",
        "/management/documents",
        params={"doc_type": doc_type, "q": search, "date_from": date_from, "date_to": date_to},
    )


@mcp.tool()
def get_document(document_id: str) -> Any:
    """Fetch one Management document including its full markdown body."""
    return _request("GET", f"/management/documents/{document_id}")


@mcp.tool()
def create_document(
    title: str,
    doc_type: str,
    content_md: str,
    doc_date: str | None = None,
    author: str | None = None,
    related_tags: str | None = None,
) -> Any:
    """Create a Management document (markdown body). doc_type is free text
    (e.g. 'memo', 'board pack'); doc_date is YYYY-MM-DD."""
    return _request(
        "POST",
        "/management/documents",
        json=_drop_none(
            {
                "title": title,
                "doc_type": doc_type,
                "content_md": content_md,
                "doc_date": doc_date,
                "author": author,
                "related_tags": related_tags,
            }
        ),
    )


@mcp.tool()
def update_document(document_id: str, updates: dict[str, Any]) -> Any:
    """Edit a Management document (partial update). Allowed keys: title,
    doc_type, content_md, doc_date (YYYY-MM-DD or null), author,
    related_tags."""
    return _request("PATCH", f"/management/documents/{document_id}", json=updates)


@mcp.tool()
def delete_document(document_id: str) -> Any:
    """DESTRUCTIVE: remove a Management document (soft delete — disappears
    from the UI)."""
    return _request("DELETE", f"/management/documents/{document_id}")


# ---------------------------------------------------------------------------
# Outside Counsel (Operations zone)
# ---------------------------------------------------------------------------

_OC = "/management/outside-counsel"


@mcp.tool()
def outside_counsel_summary(year: int | None = None) -> Any:
    """The Outside Counsel dashboard for one year (default: this year):
    budget vs actual per quarter with a band (110%+ of budget = yellow,
    125%+ = red), spend by firm and by practice area, staffing flags
    (more than 2 people billing one task on one day), firms below the
    10% discount floor or above the 5% rate-increase cap, partners who
    have left their firm, and value-ledger totals by category."""
    return _request("GET", f"{_OC}/summary", params={"year": year})


@mcp.tool()
def list_firms() -> Any:
    """List outside law firms with their chosen partners, discount %,
    hourly rate increase %, policy flags, and total spend."""
    return _request("GET", f"{_OC}/firms")


@mcp.tool()
def create_firm(
    name: str,
    discount_pct: float | str | None = None,
    rate_increase_pct: float | str | None = None,
    rate_year: int | None = None,
    notes_md: str | None = None,
) -> Any:
    """Add an outside law firm. discount_pct is the negotiated discount
    (policy minimum 10); rate_increase_pct is this year's hourly increase
    (policy cap 5) for rate_year."""
    return _request(
        "POST",
        f"{_OC}/firms",
        json=_drop_none(
            {
                "name": name,
                "discount_pct": discount_pct,
                "rate_increase_pct": rate_increase_pct,
                "rate_year": rate_year,
                "notes_md": notes_md,
            }
        ),
    )


@mcp.tool()
def update_firm(firm_id: str, updates: dict[str, Any]) -> Any:
    """Edit a firm (partial update). Allowed keys: name, discount_pct,
    rate_increase_pct, rate_year, notes_md."""
    return _request("PATCH", f"{_OC}/firms/{firm_id}", json=updates)


@mcp.tool()
def delete_firm(firm_id: str) -> Any:
    """DESTRUCTIVE: remove a firm from the UI (soft delete). Its invoices
    drop out of every spend total."""
    return _request("DELETE", f"{_OC}/firms/{firm_id}")


@mcp.tool()
def add_firm_partner(
    firm_id: str,
    name: str,
    stakeholder_id: str | None = None,
    practice_area: str | None = None,
) -> Any:
    """Record a partner the GC chose at a firm ("I hire partners, not
    firms"). stakeholder_id optionally links the partner's relationship
    dossier. practice_area: commercial | corporate | employment | ip |
    litigation | privacy | regulatory | other."""
    return _request(
        "POST",
        f"{_OC}/firms/{firm_id}/partners",
        json=_drop_none(
            {"name": name, "stakeholder_id": stakeholder_id, "practice_area": practice_area}
        ),
    )


@mcp.tool()
def update_firm_partner(partner_id: str, updates: dict[str, Any]) -> Any:
    """Edit a chosen partner (partial update). Allowed keys: name,
    stakeholder_id, practice_area, status, left_at (YYYY-MM-DD),
    resolution_note. To flag that a partner LEFT their firm set
    status='left_firm' (raises a red Urgent Matters item); resolve it
    with status='followed' (work follows them) or 'replaced' (new partner
    chosen), ideally with a resolution_note."""
    return _request("PATCH", f"{_OC}/partners/{partner_id}", json=updates)


@mcp.tool()
def delete_firm_partner(partner_id: str) -> Any:
    """DESTRUCTIVE: remove a chosen partner from a firm (soft delete)."""
    return _request("DELETE", f"{_OC}/partners/{partner_id}")


@mcp.tool()
def set_outside_counsel_budget(
    period: str,
    amount: float | str,
    practice_area: str = "all",
    notes_md: str | None = None,
) -> Any:
    """Set (insert or replace) the outside-counsel budget for one quarter.
    period: 'YYYY-Qn'. practice_area: 'all' (department total) or one of
    commercial | corporate | employment | ip | litigation | privacy |
    regulatory | other."""
    return _request(
        "PUT",
        f"{_OC}/budgets",
        json=_drop_none(
            {
                "period": period,
                "amount": amount,
                "practice_area": practice_area,
                "notes_md": notes_md,
            }
        ),
    )


@mcp.tool()
def list_outside_counsel_budgets(year: int | None = None) -> Any:
    """List outside-counsel budget rows, optionally for one year."""
    return _request("GET", f"{_OC}/budgets", params={"year": year})


@mcp.tool()
def delete_outside_counsel_budget(budget_id: str) -> Any:
    """DESTRUCTIVE: delete one budget row (permanent)."""
    return _request("DELETE", f"{_OC}/budgets/{budget_id}")


@mcp.tool()
def list_invoices(
    firm_id: str | None = None, period: str | None = None, year: int | None = None
) -> Any:
    """List outside-counsel invoices (newest first) with their lines,
    computed totals and staffing flags. Filters: firm_id, period
    ('YYYY-Qn'), year."""
    return _request(
        "GET", f"{_OC}/invoices", params={"firm_id": firm_id, "period": period, "year": year}
    )


@mcp.tool()
def create_invoice(
    firm_id: str,
    invoice_date: str,
    practice_area: str,
    lines: list[dict[str, Any]],
    invoice_number: str | None = None,
    matter_ref: str | None = None,
    status: str = "received",
    notes_md: str | None = None,
) -> Any:
    """Record an invoice line by line. invoice_date 'YYYY-MM-DD' (its
    quarter becomes the invoice's period). practice_area: commercial |
    corporate | employment | ip | litigation | privacy | regulatory |
    other. status: received | paid. Each line is {work_date
    'YYYY-MM-DD', timekeeper, title (partner | counsel | associate |
    paralegal | other), task, hours, rate}; the line amount and invoice
    total are computed by LQ.AI."""
    return _request(
        "POST",
        f"{_OC}/invoices",
        json=_drop_none(
            {
                "firm_id": firm_id,
                "invoice_date": invoice_date,
                "practice_area": practice_area,
                "lines": lines,
                "invoice_number": invoice_number,
                "matter_ref": matter_ref,
                "status": status,
                "notes_md": notes_md,
            }
        ),
    )


@mcp.tool()
def update_invoice(invoice_id: str, updates: dict[str, Any]) -> Any:
    """Edit an invoice (partial update). Allowed keys: firm_id,
    invoice_number, invoice_date, practice_area, matter_ref, status,
    notes_md, lines. Passing lines REPLACES all of the invoice's lines."""
    return _request("PATCH", f"{_OC}/invoices/{invoice_id}", json=updates)


@mcp.tool()
def delete_invoice(invoice_id: str) -> Any:
    """DESTRUCTIVE: remove an invoice from the UI and every total (soft delete)."""
    return _request("DELETE", f"{_OC}/invoices/{invoice_id}")


@mcp.tool()
def list_value_entries(year: int | None = None, category: str | None = None) -> Any:
    """List value-ledger entries. category: self_service_savings |
    billing_adjustments | insourcing_avoidance | settlement_avoidance."""
    return _request(
        "GET", f"{_OC}/value-entries", params={"year": year, "category": category}
    )


@mcp.tool()
def create_value_entry(
    period: str,
    category: str,
    amount: float | str,
    description: str,
    method_note: str,
    source: str,
    document_id: str | None = None,
) -> Any:
    """Add a value-ledger entry ("legal pays for itself"). period
    'YYYY-Qn'. category: self_service_savings | billing_adjustments |
    insourcing_avoidance | settlement_avoidance. method_note (how the
    number was calculated) and source (the record or document it traces
    to) are REQUIRED — never invent them; ask the user if unknown."""
    return _request(
        "POST",
        f"{_OC}/value-entries",
        json=_drop_none(
            {
                "period": period,
                "category": category,
                "amount": amount,
                "description": description,
                "method_note": method_note,
                "source": source,
                "document_id": document_id,
            }
        ),
    )


@mcp.tool()
def update_value_entry(entry_id: str, updates: dict[str, Any]) -> Any:
    """Edit a value-ledger entry (partial update). Allowed keys: period,
    category, amount, description, method_note, source, document_id."""
    return _request("PATCH", f"{_OC}/value-entries/{entry_id}", json=updates)


@mcp.tool()
def delete_value_entry(entry_id: str) -> Any:
    """DESTRUCTIVE: remove a value-ledger entry from the UI (soft delete)."""
    return _request("DELETE", f"{_OC}/value-entries/{entry_id}")


# ---------------------------------------------------------------------------
# Read-only overviews
# ---------------------------------------------------------------------------


@mcp.tool()
def urgent_matters() -> Any:
    """The Urgent Matters triage feed: red (act today) and yellow (act this
    week) items across commitments, stakeholder cadences, KPIs and
    outside counsel — up to 10 reds then up to 5 yellows, each with a
    why and a next step."""
    return _request("GET", "/management/urgent-matters")


@mcp.tool()
def kpi_dashboard() -> Any:
    """The full KPI dashboard in one call: department-scope KPIs grouped by
    department, plus every team member with their individual KPIs."""
    return _request("GET", "/management/dashboard")


if __name__ == "__main__":
    if not PASSWORD:
        print(
            "warning: LQ_MGMT_PASSWORD not set — tools will fail until it is. "
            "Launch via run.sh so the macOS Keychain entry is used.",
            file=sys.stderr,
        )
    mcp.run()
