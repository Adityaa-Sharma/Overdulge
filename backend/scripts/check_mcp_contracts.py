"""MCP contract guard (issue #141): fail if an adapter's tool calls drift
from the baseline recorded in `tests/contracts/`.

Every MCP platform (`mcp.swiggy.com/food`, `mcp.swiggy.com/im`,
`mcp.zepto.co.in/mcp`) returns HTTP 401 on an unauthenticated `initialize`
(confirmed via `swiggy-mcp-probe.py`, 2026-07-25), so QA's weekly regression
(`agents/qa.md` Mode B step 2) has no credential-free way to fetch a live
`tools/list` to diff against. Until a scoped, read-only service credential
is provisioned per platform (issue #141 option (a)), this script is the
documented substitute (issue #141 option (b)): it statically extracts the
tool names each adapter actually calls and diffs them against the committed
baseline, so a code change that adds, removes, or renames a tool call is
still caught — even though server-side drift on the platform's end is not.

Usage (from `backend/`): `uv run python scripts/check_mcp_contracts.py`.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# The callables an adapter invokes to reach the MCP server always take the
# tool name as their 3rd positional argument — `McpCaller`'s signature
# (`base_url, access_token, tool_name, params`) in `app/mcp/client.py`, or
# the same shape via `call_tool` directly (Zepto). Matching on the callee's
# bare name is enough: adapters never alias these under another name.
_CALLER_NAMES = {"client", "call_tool"}

# platform key -> (adapter source file, baseline contract file), both
# relative to `_BACKEND_ROOT`.
_PLATFORMS = {
    "swiggy_food": (
        Path("app/mcp/adapters/swiggy_food.py"),
        Path("tests/contracts/swiggy_food.json"),
    ),
    "swiggy_instamart": (
        Path("app/mcp/adapters/swiggy_instamart.py"),
        Path("tests/contracts/swiggy_instamart.json"),
    ),
    "zepto": (
        Path("app/mcp/adapters/zepto.py"),
        Path("tests/contracts/zepto.json"),
    ),
}


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"` assignments (e.g. Instamart's
    `_TOOL_NAME`), so a tool name referenced via a constant resolves the
    same as one passed as a literal at the call site."""
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                constants[target.id] = node.value.value
    return constants


def _resolve_string(node: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def extract_called_tool_names(adapter_path: Path) -> set[str]:
    """Every tool name an adapter module actually calls, resolved through
    simple module-level string constants where the name isn't a literal."""
    tree = ast.parse(adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path))
    constants = _string_constants(tree)
    tool_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee_name = func.id if isinstance(func, ast.Name) else None
        if callee_name is None or callee_name not in _CALLER_NAMES:
            continue
        if len(node.args) < 3:
            continue
        resolved = _resolve_string(node.args[2], constants)
        if resolved is not None:
            tool_names.add(resolved)
    return tool_names


def load_baseline_tool_names(contract_path: Path) -> set[str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return {tool["name"] for tool in contract["tools"]}


def check_platform(adapter_path: Path, contract_path: Path) -> list[str]:
    """Returns human-readable drift descriptions; empty means consistent."""
    called = extract_called_tool_names(adapter_path)
    baselined = load_baseline_tool_names(contract_path)

    violations = []
    for tool_name in sorted(called - baselined):
        violations.append(f"{adapter_path}: calls {tool_name!r}, missing from {contract_path}")
    for tool_name in sorted(baselined - called):
        violations.append(
            f"{contract_path}: lists {tool_name!r}, no longer called from {adapter_path}"
        )
    return violations


def main() -> int:
    violations: list[str] = []
    for adapter_relative, contract_relative in _PLATFORMS.values():
        violations.extend(
            check_platform(_BACKEND_ROOT / adapter_relative, _BACKEND_ROOT / contract_relative)
        )

    if violations:
        print("::error::MCP contract drift between adapter code and tests/contracts/ baseline")
        for violation in violations:
            print(violation)
        return 1
    print("MCP contract guard passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
