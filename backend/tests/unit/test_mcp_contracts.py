from __future__ import annotations

import json

import httpx
import pytest

from app.mcp import contracts
from app.mcp.adapters import swiggy_food, swiggy_instamart, zepto
from scripts.check_mcp_contracts import _TOKEN_ENV_VAR, check_platform

# --- load_contract ----------------------------------------------------------


@pytest.mark.parametrize("platform", contracts.PLATFORMS)
def test_load_contract_returns_a_well_formed_baseline(platform):
    baseline = contracts.load_contract(platform)

    assert baseline["platform"] == platform
    assert baseline["base_url"].startswith("https://")
    assert baseline["tools"], "baseline must declare at least one tool"
    for tool in baseline["tools"]:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["params"], list)


# --- diff_live_tools ---------------------------------------------------------


def _live_tool(name: str, params: list[str], required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": {param: {"type": "string"} for param in params},
            "required": required if required is not None else params,
        },
    }


def _matching_live_tools(baseline: dict) -> list[dict]:
    return [_live_tool(tool["name"], tool["params"]) for tool in baseline["tools"]]


@pytest.mark.parametrize("platform", contracts.PLATFORMS)
def test_diff_live_tools_reports_no_drift_when_live_matches_baseline(platform):
    baseline = contracts.load_contract(platform)

    findings = contracts.diff_live_tools(baseline, _matching_live_tools(baseline))

    assert findings == []


def test_diff_live_tools_flags_a_tool_missing_from_the_live_schema():
    baseline = {"tools": [{"name": "get_orders", "params": ["orderType"]}]}

    findings = contracts.diff_live_tools(baseline, [])

    assert len(findings) == 1
    assert "get_orders" in findings[0]
    assert "missing" in findings[0]


def test_diff_live_tools_flags_a_param_the_live_tool_no_longer_accepts():
    baseline = {"tools": [{"name": "get_orders", "params": ["orderType"]}]}
    live_tools = [_live_tool("get_orders", params=[])]

    findings = contracts.diff_live_tools(baseline, live_tools)

    assert len(findings) == 1
    assert "get_orders" in findings[0]
    assert "orderType" in findings[0]
    assert "no longer accepts" in findings[0]


def test_diff_live_tools_flags_a_newly_required_param_we_dont_send():
    baseline = {"tools": [{"name": "get_orders", "params": ["orderType"]}]}
    live_tools = [
        _live_tool("get_orders", params=["orderType", "region"], required=["orderType", "region"])
    ]

    findings = contracts.diff_live_tools(baseline, live_tools)

    assert len(findings) == 1
    assert "region" in findings[0]
    assert "never sends" in findings[0]


def test_diff_live_tools_ignores_new_optional_params_and_unrelated_new_tools():
    baseline = {"tools": [{"name": "get_orders", "params": ["orderType"]}]}
    live_tools = [
        _live_tool("get_orders", params=["orderType", "region"], required=["orderType"]),
        _live_tool("place_order", params=["cartId"]),
    ]

    findings = contracts.diff_live_tools(baseline, live_tools)

    assert findings == []


# --- adapter-to-baseline consistency ----------------------------------------
# Each adapter's real tool calls must be an exact match (name + param keys)
# for its committed baseline — this is what makes tests/contracts/*.json a
# living contract instead of documentation that silently rots.


def _tool_shape(calls: list[tuple]) -> set[tuple[str, frozenset[str]]]:
    return {(tool_name, frozenset(params.keys())) for _, _, tool_name, params in calls}


def _baseline_shape(platform: str) -> set[tuple[str, frozenset[str]]]:
    baseline = contracts.load_contract(platform)
    return {(tool["name"], frozenset(tool["params"])) for tool in baseline["tools"]}


def test_swiggy_food_adapter_calls_match_its_baseline():
    calls: list[tuple] = []

    def client(base_url, access_token, tool_name, params):
        calls.append((base_url, access_token, tool_name, params))
        if tool_name == "get_addresses":
            return {"addresses": [{"id": "addr1"}]}
        if tool_name == "get_food_orders":
            return {
                "orders": [
                    {
                        "orderId": "o1",
                        "orderStatus": "Delivered",
                        "orderTotal": "₹273",
                        "restaurantName": "Biryani House",
                    }
                ]
            }
        if tool_name == "get_food_order_details":
            return {"order": {"orderId": "o1", "orderedTime": "2026-02-12 00:26:00"}}
        if tool_name == "search_menu":
            return {
                "results": [
                    {
                        "name": "Chicken Biryani",
                        "price": "₹250",
                        "restaurantName": "Biryani House",
                        "restaurantId": "r1",
                    }
                ]
            }
        raise AssertionError(f"unexpected tool call: {tool_name}")

    swiggy_food.fetch_orders(client, "https://mcp.swiggy.com/food", "token")
    swiggy_food.search_menu(client, "https://mcp.swiggy.com/food", "token", "biryani")

    assert _tool_shape(calls) == _baseline_shape("swiggy_food")


def test_swiggy_instamart_adapter_calls_match_its_baseline():
    calls: list[tuple] = []

    def client(base_url, access_token, tool_name, params):
        calls.append((base_url, access_token, tool_name, params))
        if tool_name == "get_orders":
            return {
                "orders": [
                    {
                        "orderId": "im1",
                        "status": "DELIVERED",
                        "createdAt": "2026-07-20T12:00:00Z",
                        "totalAmount": 100,
                        "items": [],
                    }
                ]
            }
        if tool_name == "your_go_to_items":
            return {"items": [{"itemId": "i1", "name": "Milk", "frequencyRank": 1, "price": 40}]}
        if tool_name == "search_products":
            return {"results": [{"itemId": "i2", "name": "Bread", "price": 30}]}
        raise AssertionError(f"unexpected tool call: {tool_name}")

    swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")
    swiggy_instamart.get_usual_items(client, "https://mcp.swiggy.com/im", "token")
    swiggy_instamart.search_products(client, "https://mcp.swiggy.com/im", "token", "bread")

    assert _tool_shape(calls) == _baseline_shape("swiggy_instamart")


def test_zepto_adapter_calls_match_its_baseline():
    calls: list[tuple] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool_name = body["params"]["name"]
        arguments = body["params"]["arguments"]
        calls.append((None, None, tool_name, arguments))
        if tool_name == "list_order_history":
            result = {"orders": [{"orderId": "z1"}]}
        elif tool_name == "get_order_detail":
            result = {
                "orderId": "z1",
                "status": "DELIVERED",
                "orderedAt": "2026-07-20T12:00:00",
                "grandTotal": 10000,
                "items": [],
            }
        elif tool_name == "get_past_order_items":
            result = {
                "items": [
                    {"productVariantId": "p1", "name": "Milk", "frequencyRank": 1, "price": 4000}
                ]
            }
        elif tool_name == "search_products":
            result = {"results": [{"name": "Bread", "productVariantId": "p2", "price": 3000}]}
        else:
            raise AssertionError(f"unexpected tool call: {tool_name}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    transport = httpx.MockTransport(handler)

    zepto.fetch_orders(transport, "https://mcp.zepto.co.in/mcp", "token")
    zepto.get_usual_items(transport, "https://mcp.zepto.co.in/mcp", "token")
    zepto.search_products(transport, "https://mcp.zepto.co.in/mcp", "token", "bread")

    assert _tool_shape(calls) == _baseline_shape("zepto")


# --- check_mcp_contracts.check_platform (loud skip, not silent) -------------


@pytest.mark.parametrize("platform", contracts.PLATFORMS)
def test_check_platform_is_skipped_not_silent_without_a_token(monkeypatch, capsys, platform):
    monkeypatch.delenv(_TOKEN_ENV_VAR[platform], raising=False)

    findings = check_platform(platform)

    assert findings == []
    output = capsys.readouterr().out
    assert "SKIPPED" in output
    assert platform in output
