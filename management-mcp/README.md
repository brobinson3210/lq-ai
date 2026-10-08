# Management-tab MCP connector

An MCP (Model Context Protocol) stdio server that lets Claude Code — and,
in the future, an autonomous agent — read and edit the entries in LQ.AI's
Management tab from the terminal: stakeholders, interactions, commitments,
positions, team members, KPIs and datapoints, documents, plus the
read-only Urgent Matters and dashboard feeds.

**Status: personal tool** (not part of the Management-tab PR). It may be
proposed to the LQ community later; nothing in the LQ.AI application is
modified by it.

## Security fence

- Pure API client — no database access, no application imports. Every
  operation goes through the same authenticated `/api/v1` endpoints the
  browser uses (same owner scoping, validation, soft deletes).
- Hard allowlist in `server.py:_request`: only `/stakeholders`,
  `/stakeholder-commitments`, and `/management/` paths are reachable.
  There is no generic "call any endpoint" tool. The connector cannot
  touch anything else in LQ.AI under any circumstances.
- Credentials live in the macOS Keychain (service `lq-ai-management`),
  fetched by `run.sh` at launch — never stored in a file.
- Deletes are LQ.AI soft deletes (recoverable in the DB) and are marked
  DESTRUCTIVE in their tool descriptions.

## Setup (once)

```bash
# 1. Store the LQ.AI password in the Keychain
security add-generic-password -s lq-ai-management -a admin@lq.ai -w '<password>'

# 2. Register with Claude Code (user scope = every terminal session)
claude mcp add --scope user lq-management -- /Users/subgc2925/LQ/lq-ai/management-mcp/run.sh
```

`run.sh` finds a Python 3.10+ (bootstrapping `.venv` here on first run)
or falls back to running inside the `lqai-gates` Docker container.

Env overrides: `LQ_MGMT_BASE_URL` (default `http://localhost:8000/api/v1`
— the dev stack), `LQ_MGMT_EMAIL`, `LQ_MGMT_PASSWORD`, `LQ_MGMT_PYTHON`.

## Example prompts (in any Claude Code session)

- "Log a call with Margo — we covered the Northstar timing; mark her
  commitment about the board pack done."
- "Add a commitment: I owe Angela the revised compliance certificate by
  Friday."
- "Record Q2 2026 = 76 on the company-paper KPI."
- "What's urgent today?"

## Future: autonomous agents

Whether an agent's writes apply immediately or stage for approval is
deliberately **not decided yet** (owner's call, deferred). The tool
descriptions already flag destructive operations so MCP clients can
require confirmation.

## Testing the connector end to end

`./run-tests.sh` runs `tests/test_connector.py` against the live LQ.AI stack
over real MCP stdio. For every Management module (stakeholders, commitments,
team, KPIs, documents, outside counsel) it creates a throwaway `ZZ-TEST`
record, reads it back, updates it and deletes it, cleaning up in a `finally`
block even when a check fails. It also checks the read-only overviews and the
safety fence (path-traversal ids, plain or percent-encoded, are refused).
`--json` prints one machine-readable report; the local helper page
`mcp-tests.html` (served by the interview server on port 8765) runs it with
one button. When a new module is added, add a `check_<module>` function to
`CHECKS` in the same file.
