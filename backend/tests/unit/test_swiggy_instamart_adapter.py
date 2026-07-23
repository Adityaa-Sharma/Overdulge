from datetime import UTC, datetime

from app.mcp.adapters import swiggy_instamart


class _FakeClient:
    """Stands in for `app.mcp.client`: records every `call_tool` invocation
    so tests can assert on the exact params sent, per issue #50's AC-4.
    """

    def __init__(self, result: dict) -> None:
        self._result = result
        self.calls: list[tuple[str, str, str, dict]] = []

    def call_tool(self, base_url: str, access_token: str, tool_name: str, params: dict) -> dict:
        self.calls.append((base_url, access_token, tool_name, params))
        return self._result


def _order(**overrides) -> dict:
    fields = {
        "orderId": "im-1",
        "status": "DELIVERED",
        "orderTime": "2026-07-20T12:00:00Z",
        "grandTotal": 273,
        "items": [],
    }
    fields.update(overrides)
    return fields


def test_fetch_orders_sends_order_type_dash_and_never_instamart():
    client = _FakeClient({"orders": []})

    swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert len(client.calls) == 1
    base_url, access_token, tool_name, params = client.calls[0]
    assert base_url == "https://mcp.swiggy.com/im"
    assert access_token == "token"
    assert tool_name == "get_orders"
    assert params == {"orderType": "DASH"}
    assert params.get("orderType") != "INSTAMART"


def test_fetch_orders_converts_plain_rupees_to_paise():
    client = _FakeClient({"orders": [_order(grandTotal=273, itemTotal=250, deliveryFee=23)]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert len(orders) == 1
    order = orders[0]
    assert order.grand_total_paise == 27300
    assert order.item_total_paise == 25000
    assert order.fees_paise == 2300


def test_fetch_orders_converts_fractional_rupees_to_paise():
    client = _FakeClient({"orders": [_order(grandTotal=99.5)]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders[0].grand_total_paise == 9950


def test_fetch_orders_grand_total_paise_is_never_recomputed_from_items():
    client = _FakeClient(
        {
            "orders": [
                _order(
                    grandTotal=500,
                    items=[
                        {"name": "Chips", "quantity": 2, "price": 100},
                        {"name": "Soda", "quantity": 1, "price": 50},
                    ],
                )
            ]
        }
    )

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    # Item components (2*100 + 1*50 = 250) sum to less than grandTotal (500) —
    # if this were recomputed, it would be 25000, not 50000 (BRD §2.8).
    assert orders[0].grand_total_paise == 50000


def test_fetch_orders_parses_iso8601_utc_timestamp_directly():
    client = _FakeClient({"orders": [_order(orderTime="2026-07-20T12:00:00Z")]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders[0].ordered_at == datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def test_fetch_orders_address_id_is_always_none():
    client = _FakeClient({"orders": [_order()]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders[0].address_id is None


def test_fetch_orders_marks_cancelled_status():
    client = _FakeClient(
        {
            "orders": [
                _order(orderId="im-2", status="CANCELLED"),
                _order(orderId="im-3", status="DELIVERED"),
            ]
        }
    )

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    cancelled = {o.platform_order_id: o.is_cancelled for o in orders}
    assert cancelled == {"im-2": True, "im-3": False}


def test_fetch_orders_normalizes_items():
    client = _FakeClient(
        {
            "orders": [
                _order(
                    items=[
                        {
                            "name": "Milk",
                            "quantity": 2,
                            "price": 30,
                            "itemId": "item-1",
                            "productVariantId": "var-1",
                            "category": "dairy",
                            "isVeg": True,
                        }
                    ]
                )
            ]
        }
    )

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    item = orders[0].items[0]
    assert item.name == "Milk"
    assert item.quantity == 2
    assert item.unit_price_paise == 3000
    assert item.platform_item_id == "item-1"
    assert item.product_variant_id == "var-1"
    assert item.category == "dairy"
    assert item.is_veg is True


def test_fetch_orders_returns_empty_list_when_no_orders():
    client = _FakeClient({"orders": []})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders == []
