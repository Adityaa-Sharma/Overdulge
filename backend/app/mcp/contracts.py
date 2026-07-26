"""MCP tool contract baselines and drift diffing (issue #141, BRD §7 R-2).

A plain data + diff module — not imported by any request-handling code
path, same pattern as `app/core/nfr1_denylist.py` (ADR-0009). Baseline
contracts live as JSON under `tests/contracts/<platform>.json`, one file per
platform adapter; `tests/unit/test_mcp_contracts.py` proves each adapter's
actual tool_name/params calls match its baseline, and
`scripts/check_mcp_contracts.py` diffs the baseline against a live
`tools/list` fetch when a platform credential is available (agents/qa.md
Mode B step 2). See `tests/contracts/README.md` for what this does and does
not cover.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "contracts"

# Every platform this backend's adapters talk to (app/mcp/adapters/*).
PLATFORMS = ("swiggy_food", "swiggy_instamart", "zepto")


def load_contract(platform: str) -> dict[str, Any]:
    """Loads the committed baseline contract for `platform` (one of
    `PLATFORMS`) from `tests/contracts/<platform>.json`.
    """
    path = _CONTRACTS_DIR / f"{platform}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def diff_live_tools(baseline: dict[str, Any], live_tools: list[dict[str, Any]]) -> list[str]:
    """Compares a baseline contract's tools against a live MCP `tools/list`
    result (`[{"name": ..., "inputSchema": {"properties": {...},
    "required": [...]}}, ...]`).

    Returns one human-readable drift description per problem found; an
    empty list means the live schema still satisfies every tool this
    platform's adapter depends on. Only reports drift that could break our
    adapters — a missing tool, a param we send that the live tool no longer
    accepts, or a newly required param we never send. A live tool gaining an
    unrelated optional param, or the server exposing a brand-new tool we
    don't call, is not drift we care about.
    """
    live_by_name = {tool["name"]: tool for tool in live_tools}
    findings: list[str] = []
    for tool in baseline["tools"]:
        name = tool["name"]
        live_tool = live_by_name.get(name)
        if live_tool is None:
            findings.append(f"tool {name!r} is missing from the live schema (renamed or removed?)")
            continue
        schema = live_tool.get("inputSchema") or {}
        # A tool with no `properties` key at all has an unknown-to-us shape
        # (skip rather than guess); an explicit empty `{}` means the live
        # tool accepts zero params — that's real, checkable drift.
        schema_known = "properties" in schema
        live_properties = set((schema.get("properties") or {}).keys())
        live_required = set(schema.get("required") or [])
        baseline_params = set(tool["params"])
        for param in baseline_params:
            if schema_known and param not in live_properties:
                findings.append(f"tool {name!r} no longer accepts param {param!r}")
        for required_param in sorted(live_required - baseline_params):
            findings.append(
                f"tool {name!r} now requires param {required_param!r}, "
                "which our adapter never sends"
            )
    return findings
