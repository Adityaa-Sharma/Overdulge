# Contract tests

Stores the MCP tool schemas each platform adapter depends on: one JSON file
per platform (`swiggy_food.json`, `swiggy_instamart.json`, `zepto.json`),
each listing the tool names, call parameters, and MCP endpoint the adapter
in `backend/app/mcp/adapters/` uses. See `docs/architecture/SYSTEM.md` §5.

## Current state (issue #141)

QA's weekly regression (`agents/qa.md` Mode B step 2) is written as: fetch
live `tools/list` and diff against these files — schema drift files a P1
bug, not a silent break. That live-fetch step cannot run today:

- `mcp.swiggy.com/food`, `mcp.swiggy.com/im`, and `mcp.zepto.co.in/mcp` all
  return HTTP 401 on an unauthenticated `initialize`, before `tools/list`
  can even be attempted (confirmed via `swiggy-mcp-probe.py`, 2026-07-25).
- QA has no linked-account tokens, and Mode B explicitly forbids using live
  accounts even if it did.

So these JSON files are a **static baseline extracted from adapter code**,
not a live-fetched `tools/list` response — `baseline_source` in each file
says so explicitly. `backend/scripts/check_mcp_contracts.py` diffs each
adapter's actual tool calls (via AST, resolving simple string constants)
against its baseline file, and fails on either direction of drift: a tool
the code calls that isn't baselined, or a baselined tool the code no longer
calls. This runs offline as a normal `pytest` test
(`tests/unit/test_check_mcp_contracts.py`), so it executes in CI's existing
`pytest -q` step with no live network access and no workflow changes.

This catches code-level drift (a renamed/added/removed tool call) but not
server-side drift (the platform changing a tool's schema without the
adapter code changing) — the actual risk BRD §2 Risk R-2 names. Closing
that gap needs a scoped, read-only service credential per platform (e.g.
`mcp:tools`/`tools:read`) so QA can run an authenticated live `tools/list`
and diff it against these same files; provisioning one is a human/infra
decision (credential creation, storage, and — per BRD R-1 — Swiggy policy
review), out of scope for an agent run. Until then, this static check plus
human PR review of any adapter change is the documented substitute for
Mode B step 2.
