import inspect
import json
import re
from datetime import UTC, datetime

import httpx

from app.mcp.adapters import zepto


def _transport(list_response: dict, detail_by_order_id: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool_name = body["params"]["name"]
        arguments = body["params"]["arguments"]
        if tool_name == "list_order_history":
            result = list_response
        elif tool_name == "get_order_detail":
            result = detail_by_order_id[arguments["orderId"]]
        else:
            raise AssertionError(f"unexpected tool call: {tool_name}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return httpx.MockTransport(handler)


def _detail(**overrides) -> dict:
    fields = {
        "orderId": "z1",
        "status": "DELIVERED",
        "orderedAt": "2026-07-20T12:00:00Z",
        "grandTotal": 27300,
        "itemTotal": 25000,
        "fees": 2300,
        "items": [
            {
                "name": "Milk",
                "quantity": 2,
                "price": 6000,
                "itemId": "it1",
                "productVariantId": "pv1",
                "category": "Dairy",
                "isVeg": True,
            }
        ],
    }
    fields.update(overrides)
    return fields


def test_fetch_orders_resolves_date_via_get_order_detail():
    transport = _transport(
        {"orders": [{"orderId": "z1", "status": "DELIVERED"}]},
        {"z1": _detail()},
    )

    orders = zepto.fetch_orders(transport, "https://mcp.zepto.co.in", "access-token")

    assert len(orders) == 1
    assert orders[0].ordered_at == datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def test_fetch_orders_passes_through_integer_paise_grand_total_untouched():
    transport = _transport(
        {"orders": [{"orderId": "z1", "status": "DELIVERED"}]},
        {"z1": _detail(grandTotal=27300, itemTotal=25000, fees=2300)},
    )

    orders = zepto.fetch_orders(transport, "https://mcp.zepto.co.in", "access-token")

    assert orders[0].grand_total_paise == 27300
    assert orders[0].item_total_paise == 25000
    assert orders[0].fees_paise == 2300


def test_fetch_orders_grand_total_paise_equals_fixture_grand_total_exactly():
    detail = _detail(grandTotal=99999)
    transport = _transport({"orders": [{"orderId": "z1", "status": "DELIVERED"}]}, {"z1": detail})

    orders = zepto.fetch_orders(transport, "https://mcp.zepto.co.in", "access-token")

    assert orders[0].grand_total_paise == detail["grandTotal"]


def test_fetch_orders_sets_address_id_none_and_vendor_name_none():
    transport = _transport(
        {"orders": [{"orderId": "z1", "status": "DELIVERED"}]},
        {"z1": _detail()},
    )

    orders = zepto.fetch_orders(transport, "https://mcp.zepto.co.in", "access-token")

    assert orders[0].address_id is None
    assert orders[0].vendor_name is None


def test_fetch_orders_flags_cancelled_status():
    transport = _transport(
        {"orders": [{"orderId": "z2", "status": "CANCELLED"}]},
        {"z2": _detail(orderId="z2", status="CANCELLED")},
    )

    orders = zepto.fetch_orders(transport, "https://mcp.zepto.co.in", "access-token")

    assert orders[0].is_cancelled is True
    assert orders[0].status == "CANCELLED"


def test_fetch_orders_maps_item_fields_including_stable_product_variant_id():
    transport = _transport(
        {"orders": [{"orderId": "z1", "status": "DELIVERED"}]},
        {"z1": _detail()},
    )

    orders = zepto.fetch_orders(transport, "https://mcp.zepto.co.in", "access-token")

    item = orders[0].items[0]
    assert item.name == "Milk"
    assert item.quantity == 2
    assert item.unit_price_paise == 6000
    assert item.platform_item_id == "it1"
    assert item.product_variant_id == "pv1"
    assert item.category == "Dairy"
    assert item.is_veg is True


def test_fetch_orders_calls_get_order_detail_once_per_order_in_list():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool_name = body["params"]["name"]
        calls.append(tool_name)
        if tool_name == "list_order_history":
            result = {
                "orders": [
                    {"orderId": "z1", "status": "DELIVERED"},
                    {"orderId": "z2", "status": "DELIVERED"},
                ]
            }
        else:
            order_id = body["params"]["arguments"]["orderId"]
            result = _detail(orderId=order_id)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    orders = zepto.fetch_orders(
        httpx.MockTransport(handler), "https://mcp.zepto.co.in", "access-token"
    )

    assert len(orders) == 2
    assert calls.count("list_order_history") == 1
    assert calls.count("get_order_detail") == 2


def test_fetch_orders_empty_history_makes_no_detail_calls():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["params"]["name"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"orders": []}})

    orders = zepto.fetch_orders(
        httpx.MockTransport(handler), "https://mcp.zepto.co.in", "access-token"
    )

    assert orders == []
    assert calls == ["list_order_history"]


def test_zepto_adapter_only_calls_read_only_tools():
    # NFR-1 (BRD §2.10): this file must call exactly the read-only tools it
    # documents (history sync plus the FR-7 recommendation wrappers) and no
    # other MCP tool name.
    source = inspect.getsource(zepto)
    tool_names = re.findall(r'call_tool\(\s*[^,]+,\s*[^,]+,\s*"([^"]+)"', source)
    assert tool_names == [
        "list_order_history",
        "get_order_detail",
        "get_past_order_items",
        "search_products",
    ]


def _usual_item(**overrides) -> dict:
    fields = {
        "productVariantId": "pv1",
        "name": "Milk",
        "frequencyRank": 1,
        "price": 6000,
    }
    fields.update(overrides)
    return fields


def _search_hit(**overrides) -> dict:
    fields = {
        "productVariantId": "pv2",
        "name": "Toned Milk 1L",
        "price": 5500,
    }
    fields.update(overrides)
    return fields


def _tool_transport(result_by_tool: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool_name = body["params"]["name"]
        result = result_by_tool[tool_name]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return httpx.MockTransport(handler)


def test_get_usual_items_normalizes_frequency_ranking_and_join_key():
    transport = _tool_transport({"get_past_order_items": {"items": [_usual_item()]}})

    items = zepto.get_usual_items(transport, "https://mcp.zepto.co.in", "access-token")

    assert len(items) == 1
    item = items[0]
    assert item.product_variant_id == "pv1"
    assert item.name == "Milk"
    assert item.frequency_rank == 1
    assert item.unit_price_paise == 6000


def test_get_usual_items_builds_zepto_product_redirect_url():
    transport = _tool_transport(
        {
            "get_past_order_items": {
                "items": [_usual_item(name="Toned Milk", productVariantId="pv9")]
            }
        }
    )

    items = zepto.get_usual_items(transport, "https://mcp.zepto.co.in", "access-token")

    assert items[0].redirect_url == "https://www.zeptonow.com/pn/toned-milk/pvid/pv9"


def test_get_usual_items_returns_empty_list_when_no_items():
    transport = _tool_transport({"get_past_order_items": {"items": []}})

    items = zepto.get_usual_items(transport, "https://mcp.zepto.co.in", "access-token")

    assert items == []


def test_search_products_sends_the_query_and_normalizes_results():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["params"]["arguments"])
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"results": [_search_hit()]},
            },
        )

    items = zepto.search_products(
        httpx.MockTransport(handler), "https://mcp.zepto.co.in", "access-token", "milk"
    )

    assert calls == [{"query": "milk"}]
    assert len(items) == 1
    assert items[0].name == "Toned Milk 1L"
    assert items[0].unit_price_paise == 5500
    assert items[0].raw == _search_hit()


def test_search_products_builds_redirect_url_from_platform_shape():
    transport = _tool_transport(
        {
            "search_products": {
                "results": [_search_hit(name="Amul Butter 100g", productVariantId="pv7")]
            }
        }
    )

    items = zepto.search_products(transport, "https://mcp.zepto.co.in", "access-token", "butter")

    assert items[0].redirect_url == "https://www.zeptonow.com/pn/amul-butter-100g/pvid/pv7"


def test_search_products_returns_empty_list_when_no_results():
    transport = _tool_transport({"search_products": {"results": []}})

    items = zepto.search_products(transport, "https://mcp.zepto.co.in", "access-token", "xyz")

    assert items == []
