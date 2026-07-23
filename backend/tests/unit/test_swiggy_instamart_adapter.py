from datetime import UTC, datetime

from app.mcp.adapters import swiggy_instamart


def _make_client(result: dict):
    """A plain callable matching `mcp/client.py::call_tool`'s signature —
    the same shape `swiggy_instamart.fetch_orders` receives from real
    orchestration, so tests can assert on the exact params sent (issue #50's
    AC-4) without inventing an interface no real caller has.
    """
    calls: list[tuple[str, str, str, dict]] = []

    def client(base_url: str, access_token: str, tool_name: str, params: dict) -> dict:
        calls.append((base_url, access_token, tool_name, params))
        return result

    return client, calls


def _order(
    *,
    order_id: str = "im-1",
    status: str = "DELIVERED",
    created_at: str = "2026-07-20T12:00:00Z",
    total_amount: float = 273,
    item_total: float | None = None,
    delivery_fee: float | None = None,
    items: list | None = None,
) -> dict:
    # Mirrors the live get_orders shape: `createdAt` timestamp, top-level
    # `totalAmount`, and the itemised money under `billDetails`.
    order = {
        "orderId": order_id,
        "status": status,
        "createdAt": created_at,
        "totalAmount": total_amount,
        "storeName": "Instamart",
        "items": items if items is not None else [],
    }
    bill: dict = {"grandTotal": total_amount}
    if item_total is not None:
        bill["itemTotal"] = item_total
    if delivery_fee is not None:
        bill["deliveryFee"] = delivery_fee
    order["billDetails"] = bill
    return order


def test_fetch_orders_sends_order_type_dash_and_never_instamart():
    client, calls = _make_client({"orders": []})

    swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert len(calls) == 1
    base_url, access_token, tool_name, params = calls[0]
    assert base_url == "https://mcp.swiggy.com/im"
    assert access_token == "token"
    assert tool_name == "get_orders"
    assert params == {"orderType": "DASH"}
    assert params.get("orderType") != "INSTAMART"


def test_fetch_orders_converts_plain_rupees_to_paise():
    client, _ = _make_client(
        {"orders": [_order(total_amount=273, item_total=250, delivery_fee=23)]}
    )

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert len(orders) == 1
    order = orders[0]
    assert order.grand_total_paise == 27300
    assert order.item_total_paise == 25000
    assert order.fees_paise == 2300


def test_fetch_orders_converts_fractional_rupees_to_paise():
    client, _ = _make_client({"orders": [_order(total_amount=99.5)]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders[0].grand_total_paise == 9950


def test_fetch_orders_grand_total_paise_is_never_recomputed_from_items():
    client, _ = _make_client(
        {
            "orders": [
                _order(
                    total_amount=500,
                    items=[
                        {"name": "Chips", "quantity": 2, "price": 100},
                        {"name": "Soda", "quantity": 1, "price": 50},
                    ],
                )
            ]
        }
    )

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    # Item components (2*100 + 1*50 = 250) sum to less than the total (500) —
    # if this were recomputed, it would be 25000, not 50000 (BRD §2.8).
    assert orders[0].grand_total_paise == 50000


def test_fetch_orders_parses_iso8601_utc_timestamp_directly():
    client, _ = _make_client({"orders": [_order(created_at="2026-07-20T12:00:00Z")]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders[0].ordered_at == datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def test_fetch_orders_tolerates_an_order_with_no_bill_details():
    # billDetails is the only source of item/fee totals; an order missing it
    # must still normalize, with those fields left None.
    order = _order()
    del order["billDetails"]
    client, _ = _make_client({"orders": [order]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders[0].grand_total_paise == 27300
    assert orders[0].item_total_paise is None
    assert orders[0].fees_paise is None


def test_fetch_orders_address_id_is_always_none():
    client, _ = _make_client({"orders": [_order()]})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders[0].address_id is None


def test_fetch_orders_marks_cancelled_status():
    client, _ = _make_client(
        {
            "orders": [
                _order(order_id="im-2", status="CANCELLED"),
                _order(order_id="im-3", status="DELIVERED"),
            ]
        }
    )

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    cancelled = {o.platform_order_id: o.is_cancelled for o in orders}
    assert cancelled == {"im-2": True, "im-3": False}


def test_fetch_orders_normalizes_items():
    client, _ = _make_client(
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


def test_fetch_orders_handles_items_without_a_price():
    # The live get_orders items carry only name/quantity/itemId — no price.
    client, _ = _make_client(
        {"orders": [_order(items=[{"name": "Elaneer", "quantity": 1, "itemId": "I4YVYP7HGB"}])]}
    )

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    item = orders[0].items[0]
    assert item.name == "Elaneer"
    assert item.unit_price_paise is None


def test_fetch_orders_returns_empty_list_when_no_orders():
    client, _ = _make_client({"orders": []})

    orders = swiggy_instamart.fetch_orders(client, "https://mcp.swiggy.com/im", "token")

    assert orders == []
