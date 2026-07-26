# Contract tests

Stores the MCP tool schemas each platform adapter depends on: `swiggy_food.json`,
`swiggy_instamart.json`, `zepto.json` — one per `app/mcp/adapters/*` module.
Each file lists the tools that module calls, the request params it sends per
tool, and (as `notes`, informational only) the response fields it reads.
Never call live platforms from CI. See `docs/architecture/SYSTEM.md` §5.

## What's checked automatically today

`app/mcp/contracts.py` loads these baselines and diffs them against a live
`tools/list` result. `tests/unit/test_mcp_contracts.py` proves two things on
every run, with no live network access:

1. **The baseline is real, not aspirational** — for each adapter, a
   consistency test drives `fetch_orders`/`get_usual_items`/`search_*`
   through a fake client and asserts the exact set of (tool name, param
   keys) it calls matches the committed JSON file. If a developer renames a
   tool, adds a param, or drops one without updating the baseline, this
   test fails — the baseline can no longer silently rot.
2. **`diff_live_tools` correctly flags real drift** — missing tools, params
   a live tool no longer accepts, and newly-required params our adapter
   doesn't send are all covered by fixture-based unit tests.

## What still requires a human decision (issue #141)

QA's weekly regression (`agents/qa.md` Mode B step 2) is meant to fetch a
**live** `tools/list` and diff it against these baselines — the check above
only proves internal consistency, it cannot detect Swiggy/Zepto changing
their schema server-side. That live half is still blocked on two things
neither this repo's Developer agent role nor CI is able to resolve on its
own:

- None of the three MCP servers permit unauthenticated `tools/list`
  (`initialize` itself returns 401 — see `swiggy-mcp-probe.py`). A live
  check needs a per-platform, read-only service credential.
- Even once a credential exists, wiring it in as a secret requires editing
  `.github/workflows/qa-regression.yml` and/or `agents/qa.md` — both are
  off-limits to the Developer agent (`agents/dev.md` "Rules").

`scripts/check_mcp_contracts.py` implements the live-fetch/diff path so it's
ready the moment a credential exists: run it with `SWIGGY_FOOD_MCP_TOKEN` /
`SWIGGY_INSTAMART_MCP_TOKEN` / `ZEPTO_MCP_TOKEN` set in the environment and
it performs a read-only `initialize` + `tools/list` per platform and prints
`DRIFT`/`OK`/`ERROR`. Without a token it prints `SKIPPED <platform>` and
exits 0 — loud and attributable, never a quiet no-op. Until a human
provisions those credentials and wires them into the QA workflow, BRD §7
R-2's live-drift mitigation stays partial: the code-side contract can't go
stale, but a live Swiggy/Zepto-side schema change won't be caught until
someone with adapter code access runs the script manually with a real
token, or a hand-fetched `tools/list` dump.
