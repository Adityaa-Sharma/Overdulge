"""MCP contract check (issue #141, agents/qa.md Mode B step 2): diffs each
platform's committed `tests/contracts/<platform>.json` baseline against a
live, read-only `tools/list` call.

Only calls `initialize` and `tools/list` — never `tools/call` — so NFR-1's
read-only guarantee is not at risk from this script.

Fetching `tools/list` requires an authenticated session; none of the three
MCP servers permit unauthenticated schema discovery (confirmed by
`swiggy-mcp-probe.py`). This script reads one bearer token per platform from
an env var (see `_TOKEN_ENV_VAR`) — none are configured in CI/QA today.
Provisioning a read-only service credential per platform and wiring it into
`.github/workflows/qa-regression.yml` as a secret is an ops/human decision
outside this script's — and this repo's Developer agent's — scope (see
`tests/contracts/README.md`). Without a token, a platform is loudly SKIPPED,
never silently treated as "no drift".

Usage (from `backend/`): `uv run python scripts/check_mcp_contracts.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.mcp.contracts import PLATFORMS, diff_live_tools, load_contract  # noqa: E402

_TOKEN_ENV_VAR = {
    "swiggy_food": "SWIGGY_FOOD_MCP_TOKEN",
    "swiggy_instamart": "SWIGGY_INSTAMART_MCP_TOKEN",
    "zepto": "ZEPTO_MCP_TOKEN",
}
_TIMEOUT = 15
_PROTOCOL_VERSION = "2025-06-18"


def _fetch_live_tools(base_url: str, token: str) -> list[dict[str, Any]]:
    """Read-only MCP handshake: `initialize` then `tools/list`."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=_TIMEOUT) as client:
        init = client.post(
            base_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "overdulge-contract-check", "version": "0.1"},
                },
            },
        )
        init.raise_for_status()
        session_id = init.headers.get("Mcp-Session-Id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        listed = client.post(
            base_url,
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        listed.raise_for_status()
        return listed.json().get("result", {}).get("tools", [])


def check_platform(platform: str) -> list[str]:
    """Returns drift findings for `platform`. A skipped (no-token) platform
    returns no findings, but the skip itself is always printed — this must
    never look identical to a clean pass in the output.
    """
    baseline = load_contract(platform)
    env_var = _TOKEN_ENV_VAR[platform]
    token = os.environ.get(env_var)
    if not token:
        print(f"SKIPPED {platform}: no {env_var} configured — live drift check not run today")
        return []

    try:
        live_tools = _fetch_live_tools(baseline["base_url"], token)
    except httpx.HTTPError as exc:
        message = f"live tools/list fetch failed for {platform}: {exc}"
        print(f"ERROR {platform}: {message}")
        return [message]

    findings = diff_live_tools(baseline, live_tools)
    if findings:
        print(f"DRIFT {platform}:")
        for finding in findings:
            print(f"  - {finding}")
    else:
        print(f"OK {platform}: live schema matches the baseline ({len(baseline['tools'])} tools)")
    return findings


def main() -> int:
    all_findings: list[str] = []
    for platform in PLATFORMS:
        all_findings.extend(check_platform(platform))
    if all_findings:
        print("::error::MCP schema drift detected — file a P1 type:bug (BRD §7 R-2)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
