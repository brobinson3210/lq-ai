"""End-to-end test suite for the Management-tab MCP connector.

Proves, against the LIVE LQ.AI stack, that every Management module can
be created, read back, updated and deleted through the connector —
exactly the path Claude (and later the GC agent) uses. It speaks real
MCP over stdio to ``server.py``, so a pass means the tool schemas, the
fence, the login, and the API all work together.

Every record it creates is a throwaway tagged ``ZZ-TEST`` and is deleted
in a ``finally`` block even when a check fails, so the demo data is
never left with junk (the lesson of the 7/29 "Grand Wizard" record).

Run (normally via ``run-tests.sh``, which supplies the Keychain password):

    python tests/test_connector.py           # human-readable report
    python tests/test_connector.py --json    # one JSON object (for the web page)

Exit status is 0 only when every module passes.

Adding a module: write one ``async def check_<module>(t)`` that creates,
reads back, updates and deletes through ``t.call``, and append it to
``CHECKS``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
TAG = "ZZ-TEST"
# A period far from real data so budget checks never collide with it.
TEST_PERIOD = "2099-Q4"


class CheckFailed(AssertionError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


@dataclass
class Tester:
    session: ClientSession
    cleanups: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    async def call(self, tool: str, **args: Any) -> Any:
        """Call one tool; raise with the server's message on a tool error."""

        result = await self.session.call_tool(tool, args)
        texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        if result.isError:
            raise CheckFailed(f"{tool} failed: {' '.join(texts)[:400]}")
        self.steps.append(tool)
        # FastMCP 1.x returns a list as one content block per item.
        parsed = []
        for t in texts:
            try:
                parsed.append(json.loads(t))
            except json.JSONDecodeError:
                parsed.append(t)
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return parsed[0] if len(parsed) == 1 else parsed

    async def expect_error(self, tool: str, needle: str, **args: Any) -> None:
        result = await self.session.call_tool(tool, args)
        texts = " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        expect(result.isError, f"{tool} should have been refused")
        expect(needle.lower() in texts.lower(), f"{tool} error did not mention {needle!r}: {texts[:200]}")
        self.steps.append(f"{tool} (refused as expected)")

    def cleanup(self, tool: str, **args: Any) -> None:
        """Register a delete to run in ``finally`` (latest first)."""

        self.cleanups.append((tool, args))

    def done(self, tool: str, **args: Any) -> None:
        """The check deleted it itself — drop the pending cleanup."""

        if (tool, args) in self.cleanups:
            self.cleanups.remove((tool, args))

    async def run_cleanups(self) -> list[str]:
        errors = []
        for tool, args in reversed(self.cleanups):
            result = await self.session.call_tool(tool, args)
            if result.isError:
                texts = " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                if "404" not in texts:
                    errors.append(f"cleanup {tool} failed: {texts[:200]}")
        self.cleanups.clear()
        return errors


def as_list(value: Any) -> list[Any]:
    """A list tool's result: FastMCP 1.x unwraps a one-item list to the item."""

    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def ids(rows: Any) -> set[str]:
    return {r["id"] for r in as_list(rows) if isinstance(r, dict) and "id" in r}


# ---------------------------------------------------------------------------
# Checks — one per module
# ---------------------------------------------------------------------------


async def check_stakeholders(t: Tester) -> None:
    s = await t.call("create_stakeholder", full_name=f"{TAG} Stakeholder", stakeholder_type="other")
    t.cleanup("delete_stakeholder", stakeholder_id=s["id"])
    got = await t.call("get_stakeholder", stakeholder_id=s["id"])
    expect(got["full_name"] == f"{TAG} Stakeholder", "stakeholder did not read back")
    upd = await t.call(
        "update_stakeholder", stakeholder_id=s["id"], updates={"organization": f"{TAG} Org"}
    )
    expect(upd["organization"] == f"{TAG} Org", "stakeholder update did not stick")
    await t.call("log_interaction", stakeholder_id=s["id"], summary_md=f"{TAG} check-in call")
    interactions = await t.call("list_interactions", stakeholder_id=s["id"])
    expect(len(as_list(interactions)) >= 1, "interaction did not read back")
    await t.call("delete_stakeholder", stakeholder_id=s["id"])
    t.done("delete_stakeholder", stakeholder_id=s["id"])
    expect(s["id"] not in ids(await t.call("list_stakeholders")), "stakeholder still listed")


async def check_commitments_positions(t: Tester) -> None:
    s = await t.call("create_stakeholder", full_name=f"{TAG} Counterparty", stakeholder_type="other")
    t.cleanup("delete_stakeholder", stakeholder_id=s["id"])
    c = await t.call(
        "add_commitment",
        stakeholder_id=s["id"],
        direction="we_owe",
        description=f"{TAG} send the memo",
        due_date="2099-12-31",
    )
    rows = await t.call("list_commitments", stakeholder_id=s["id"])
    expect(c["id"] in ids(rows), "commitment did not read back")
    upd = await t.call("update_commitment", commitment_id=c["id"], updates={"status": "done"})
    expect(upd["status"] == "done", "commitment update did not stick")
    await t.call("add_position", stakeholder_id=s["id"], topic=f"{TAG} topic", stance="supportive")
    positions = await t.call("list_positions", stakeholder_id=s["id"])
    expect(len(as_list(positions)) >= 1, "position did not read back")


async def check_team_members(t: Tester) -> None:
    m = await t.call(
        "create_team_member", name=f"{TAG} Member", role_title="Counsel", department="legal"
    )
    t.cleanup("delete_team_member", team_member_id=m["id"])
    expect(m["id"] in ids(await t.call("list_team_members")), "team member did not read back")
    upd = await t.call(
        "update_team_member", team_member_id=m["id"], updates={"seniority": "Senior"}
    )
    expect(upd["seniority"] == "Senior", "team member update did not stick")
    await t.call("delete_team_member", team_member_id=m["id"])
    t.done("delete_team_member", team_member_id=m["id"])
    expect(m["id"] not in ids(await t.call("list_team_members")), "team member still listed")


async def check_kpis(t: Tester) -> None:
    k = await t.call(
        "create_kpi",
        name=f"{TAG} KPI",
        department="legal",
        scope="department",
        unit="days",
        cadence="quarterly",
        direction="lower_is_better",
        target=5,
    )
    t.cleanup("delete_kpi", kpi_id=k["id"])
    await t.call("record_kpi_datapoint", kpi_id=k["id"], period=TEST_PERIOD, value=7)
    got = await t.call("get_kpi", kpi_id=k["id"])
    expect(float(got["latest_value"]) == 7, f"datapoint did not read back ({got['latest_value']})")
    upd = await t.call("update_kpi", kpi_id=k["id"], updates={"target": "6"})
    expect(float(upd["target"]) == 6, "KPI update did not stick")
    await t.call("delete_kpi", kpi_id=k["id"])
    t.done("delete_kpi", kpi_id=k["id"])
    expect(k["id"] not in ids(await t.call("list_kpis")), "KPI still listed")


async def check_documents(t: Tester) -> None:
    d = await t.call(
        "create_document", title=f"{TAG} Document", doc_type="memo", content_md="Test body."
    )
    t.cleanup("delete_document", document_id=d["id"])
    got = await t.call("get_document", document_id=d["id"])
    expect(got["content_md"] == "Test body.", "document did not read back")
    upd = await t.call("update_document", document_id=d["id"], updates={"author": TAG})
    expect(upd["author"] == TAG, "document update did not stick")
    await t.call("delete_document", document_id=d["id"])
    t.done("delete_document", document_id=d["id"])


async def check_outside_counsel(t: Tester) -> None:
    firm = await t.call("create_firm", name=f"{TAG} Firm LLP", discount_pct=8, rate_increase_pct=6)
    t.cleanup("delete_firm", firm_id=firm["id"])
    expect(firm["below_discount_floor"] is True, "8% discount should be below the floor")
    expect(firm["above_increase_cap"] is True, "6% increase should be above the cap")
    upd = await t.call("update_firm", firm_id=firm["id"], updates={"discount_pct": "10"})
    expect(upd["below_discount_floor"] is False, "firm update did not clear the discount flag")

    partner = await t.call("add_firm_partner", firm_id=firm["id"], name=f"{TAG} Partner")
    left = await t.call(
        "update_firm_partner", partner_id=partner["id"], updates={"status": "left_firm"}
    )
    expect(left["status"] == "left_firm" and left["left_at"], "partner not marked as left")
    summary = await t.call("outside_counsel_summary")
    expect(
        any(a["partner_id"] == partner["id"] for a in summary["partner_alerts"]),
        "a departed partner should be flagged in the summary",
    )
    urgent = await t.call("urgent_matters")
    if len(urgent["red"]) < 10:  # the red band is capped at 10
        expect(
            any(f"{TAG} Partner" in i["title"] for i in urgent["red"]),
            "a departed partner should be a red Urgent Matters item",
        )
    await t.call("update_firm_partner", partner_id=partner["id"], updates={"status": "replaced"})

    budget = await t.call("set_outside_counsel_budget", period=TEST_PERIOD, amount=1000)
    t.cleanup("delete_outside_counsel_budget", budget_id=budget["id"])
    again = await t.call("set_outside_counsel_budget", period=TEST_PERIOD, amount=2000)
    expect(again["id"] == budget["id"], "budget upsert created a duplicate")

    line = {
        "work_date": "2099-11-02",
        "timekeeper": f"{TAG} A",
        "title": "associate",
        "task": "Call",
        "hours": 1,
        "rate": 700,
    }
    lines = [line, {**line, "timekeeper": f"{TAG} B"}, {**line, "timekeeper": f"{TAG} C"}]
    inv = await t.call(
        "create_invoice",
        firm_id=firm["id"],
        invoice_date="2099-11-30",
        practice_area="commercial",
        lines=lines,
        invoice_number=f"{TAG}-1",
    )
    t.cleanup("delete_invoice", invoice_id=inv["id"])
    expect(inv["total"] == "2100.00", f"invoice total wrong ({inv['total']})")
    expect(len(inv["staffing_flags"]) == 1, "3 billers on one task should be flagged")
    upd_inv = await t.call(
        "update_invoice", invoice_id=inv["id"], updates={"lines": lines[:2], "status": "paid"}
    )
    expect(upd_inv["staffing_flags"] == [], "2 billers should not be flagged")
    rows = await t.call("list_invoices", period=TEST_PERIOD)
    expect(inv["id"] in ids(rows), "invoice did not read back")

    summary = await t.call("outside_counsel_summary", year=2099)
    q4 = next(q for q in summary["quarters"] if q["period"] == TEST_PERIOD)
    expect(q4["actual"] == "1400.00" and q4["budget"] == "2000.00", "summary totals wrong")

    entry = await t.call(
        "create_value_entry",
        period=TEST_PERIOD,
        category="billing_adjustments",
        amount=100,
        description=f"{TAG} write-down",
        method_note="Test method",
        source="Test source",
    )
    t.cleanup("delete_value_entry", entry_id=entry["id"])
    await t.expect_error(
        "create_value_entry",
        "422",
        period=TEST_PERIOD,
        category="billing_adjustments",
        amount=100,
        description=f"{TAG} no receipt",
        method_note="Test method",
        source=" ",
    )
    upd_entry = await t.call("update_value_entry", entry_id=entry["id"], updates={"amount": "150"})
    expect(upd_entry["amount"] == "150.00", "value entry update did not stick")

    for tool, key, value in (
        ("delete_value_entry", "entry_id", entry["id"]),
        ("delete_invoice", "invoice_id", inv["id"]),
        ("delete_outside_counsel_budget", "budget_id", budget["id"]),
        ("delete_firm", "firm_id", firm["id"]),
    ):
        await t.call(tool, **{key: value})
        t.done(tool, **{key: value})
    expect(firm["id"] not in ids(await t.call("list_firms")), "firm still listed")


async def check_overviews(t: Tester) -> None:
    urgent = await t.call("urgent_matters")
    expect({"red", "yellow"} <= set(urgent), "urgent_matters shape changed")
    expect(len(urgent["red"]) <= 10 and len(urgent["yellow"]) <= 5, "urgent caps exceeded")
    dash = await t.call("kpi_dashboard")
    expect("departments" in dash and "team" in dash, "kpi_dashboard shape changed")
    summary = await t.call("outside_counsel_summary")
    expect(len(summary["quarters"]) == 4, "outside_counsel_summary shape changed")


async def check_fence(t: Tester) -> None:
    # No tool can reach outside the Management tab — including ids
    # crafted to path-traverse, plain or percent-encoded.
    for tool, args in (
        ("get_kpi", {"kpi_id": "../../users/me"}),
        ("get_stakeholder", {"stakeholder_id": "../users/me"}),
        ("get_document", {"document_id": "..%2F..%2Fusers%2Fme"}),
        ("update_kpi", {"kpi_id": "../../admin/users", "updates": {}}),
    ):
        await t.expect_error(tool, "Blocked", **args)
    tools = await t.session.list_tools()
    names = {tool.name for tool in tools.tools}
    expect(not any("request" in n or "endpoint" in n for n in names), "a generic-call tool exists")
    t.steps.append(f"{len(names)} tools listed; no generic call tool")


CHECKS: list[tuple[str, Callable[[Tester], Awaitable[None]]]] = [
    ("Stakeholders & interactions", check_stakeholders),
    ("Commitments & positions", check_commitments_positions),
    ("Team members", check_team_members),
    ("KPIs & datapoints", check_kpis),
    ("Documents", check_documents),
    ("Outside Counsel", check_outside_counsel),
    ("Urgent Matters & dashboards", check_overviews),
    ("Safety fence", check_fence),
]


async def run_all() -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        env={**os.environ},
    )
    results = []
    started = time.time()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, check in CHECKS:
                tester = Tester(session)
                t0 = time.time()
                error = None
                try:
                    await check(tester)
                except CheckFailed as exc:
                    error = str(exc)
                except Exception as exc:  # noqa: BLE001 — report, don't crash the suite
                    error = f"{type(exc).__name__}: {exc}"
                    traceback.print_exc(file=sys.stderr)
                finally:
                    cleanup_errors = await tester.run_cleanups()
                if cleanup_errors:
                    error = "; ".join(filter(None, [error, *cleanup_errors]))
                results.append(
                    {
                        "module": name,
                        "ok": error is None,
                        "error": error,
                        "tool_calls": len(tester.steps),
                        "seconds": round(time.time() - t0, 2),
                    }
                )
    return {
        "ok": all(r["ok"] for r in results),
        "passed": sum(r["ok"] for r in results),
        "total": len(results),
        "seconds": round(time.time() - started, 1),
        "results": results,
    }


def main() -> int:
    report = asyncio.run(run_all())
    if "--json" in sys.argv:
        print(json.dumps(report))
    else:
        for r in report["results"]:
            mark = "PASS" if r["ok"] else "FAIL"
            print(f"{mark}  {r['module']}  ({r['tool_calls']} tool calls, {r['seconds']}s)")
            if r["error"]:
                print(f"      {r['error']}")
        print(f"\n{report['passed']}/{report['total']} modules passed in {report['seconds']}s")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
