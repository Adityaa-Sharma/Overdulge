from __future__ import annotations

import json

from scripts.check_mcp_contracts import (
    _BACKEND_ROOT,
    _PLATFORMS,
    check_platform,
    extract_called_tool_names,
    load_baseline_tool_names,
)


def test_committed_baselines_match_current_adapter_code():
    """The baseline this PR commits must already be drift-free against the
    adapter code it was extracted from — this is the regression a future
    tool-name change (add/remove/rename) should trip."""
    for platform, (adapter_relative, contract_relative) in _PLATFORMS.items():
        violations = check_platform(
            _BACKEND_ROOT / adapter_relative, _BACKEND_ROOT / contract_relative
        )
        assert violations == [], f"{platform} baseline drifted: {violations}"


def test_extract_called_tool_names_resolves_module_level_constants(tmp_path):
    """Instamart's `get_orders` call goes through a `_TOOL_NAME` constant
    rather than a literal — the extractor must resolve it, not miss it."""
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        '_TOOL_NAME = "get_orders"\n'
        "\n"
        "def fetch_orders(client, base_url, access_token):\n"
        '    return client(base_url, access_token, _TOOL_NAME, {"orderType": "DASH"})\n'
    )

    assert extract_called_tool_names(adapter) == {"get_orders"}


def test_check_platform_flags_tool_called_but_not_baselined(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "def fetch_orders(client, base_url, access_token):\n"
        '    return client(base_url, access_token, "get_orders", {})\n'
        '    return client(base_url, access_token, "new_undocumented_tool", {})\n'
    )
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"tools": [{"name": "get_orders", "params": []}]}))

    violations = check_platform(adapter, contract)

    assert len(violations) == 1
    assert "new_undocumented_tool" in violations[0]
    assert "missing from" in violations[0]


def test_check_platform_flags_stale_baseline_entry(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "def fetch_orders(client, base_url, access_token):\n"
        '    return client(base_url, access_token, "get_orders", {})\n'
    )
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "tools": [
                    {"name": "get_orders", "params": []},
                    {"name": "retired_tool", "params": []},
                ]
            }
        )
    )

    violations = check_platform(adapter, contract)

    assert len(violations) == 1
    assert "retired_tool" in violations[0]
    assert "no longer called" in violations[0]


def test_load_baseline_tool_names(tmp_path):
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"tools": [{"name": "search_products", "params": ["query"]}]}))

    assert load_baseline_tool_names(contract) == {"search_products"}
