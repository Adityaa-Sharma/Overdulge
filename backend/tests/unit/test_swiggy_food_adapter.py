import inspect
from datetime import UTC, datetime

import pytest

from app.mcp.adapters import swiggy_food
from app.mcp.adapters.swiggy_food import fetch_orders

_BASE_URL = "https://mcp.swiggy.com/food"
_ACCESS_TOKEN = "access-token"


def _make_client(addresses, orders_by_address, details_by_order_id):
    calls: list[tuple[str, str, str, dict]] = []

    def client(base_url, access_token, tool_name, params):
        calls.append((base_url, access_token, tool_name, params))
        if tool_name == "get_addresses":
            return {"addresses": addresses}
        if tool_name == "get_food_orders":
            return {"orders": orders_by_address[params["addressId"]]}
        if tool_name == "get_food_order_details":
            return details_by_order_id[params["orderId"]]
        raise AssertionError(f"unexpected tool call: {tool_name}")

    return client, calls


def _raw_order(order_id, **overrides):
    # Field names/values match the live get_food_orders response: the total is
    # `orderTotal` (a "₹"-formatted string) and the status is title-case.
    fields = {
        "orderId": order_id,
        "orderStatus": "Delivered",
        "orderedTime": "February 12, 0:26 AM",
        "orderTotal": "₹273",
        "restaurantName": "Biryani House",
    }
    fields.update(overrides)
    return fields


def _detail(order_id, ordered_time="2026-02-12 00:26:00"):
    # get_food_order_details nests the order under `order`, and `orderedTime` is
    # naive IST wall-clock (no offset) — as the live API returns it.
    return {"order": {"orderId": order_id, "orderedTime": ordered_time}}


def test_fetch_orders_merges_orders_from_two_addresses_with_no_duplicates():
    client, calls = _make_client(
        addresses=[{"id": "addr1"}, {"id": "addr2"}],
        orders_by_address={
            "addr1": [_raw_order("o1")],
            "addr2": [_raw_order("o2")],
        },
        details_by_order_id={"o1": _detail("o1"), "o2": _detail("o2")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert {order.platform_order_id for order in orders} == {"o1", "o2"}
    by_id = {order.platform_order_id: order for order in orders}
    assert by_id["o1"].address_id == "addr1"
    assert by_id["o2"].address_id == "addr2"
    address_call_params = [call[3] for call in calls if call[2] == "get_food_orders"]
    # The address's `id` is passed back to get_food_orders as `addressId`.
    assert address_call_params == [{"addressId": "addr1"}, {"addressId": "addr2"}]


def test_fetch_orders_keeps_first_address_when_order_id_repeats():
    # BRD §2.4: an order cannot legitimately appear under two addresses, but
    # the merge must not duplicate rows if it ever does.
    client, _ = _make_client(
        addresses=[{"id": "addr1"}, {"id": "addr2"}],
        orders_by_address={
            "addr1": [_raw_order("o1")],
            "addr2": [_raw_order("o1")],
        },
        details_by_order_id={"o1": _detail("o1")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert len(orders) == 1
    assert orders[0].address_id == "addr1"


def test_fetch_orders_calls_get_food_order_details_for_every_order():
    client, calls = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [_raw_order("o1"), _raw_order("o2")]},
        details_by_order_id={"o1": _detail("o1"), "o2": _detail("o2")},
    )

    fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    detail_call_params = [call[3] for call in calls if call[2] == "get_food_order_details"]
    assert {params["orderId"] for params in detail_call_params} == {"o1", "o2"}


def test_fetch_orders_resolves_year_via_order_details():
    client, _ = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [_raw_order("o1")]},
        details_by_order_id={"o1": _detail("o1", ordered_time="2026-02-12T00:26:00+05:30")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert orders[0].ordered_at == datetime(2026, 2, 11, 18, 56, tzinfo=UTC)


def test_fetch_orders_treats_a_naive_detail_timestamp_as_ist():
    # The live API returns naive wall-clock IST ("2026-02-12 00:26:00"); it
    # must be read as IST, not as UTC, or every order lands 5.5h early.
    client, _ = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [_raw_order("o1")]},
        details_by_order_id={"o1": _detail("o1", ordered_time="2026-02-12 00:26:00")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert orders[0].ordered_at == datetime(2026, 2, 11, 18, 56, tzinfo=UTC)


@pytest.mark.parametrize(
    "formatted,expected_paise",
    [
        ("₹273", 27300),
        ("₹1,234", 123400),
        ("₹99.50", 9950),
        ("₹0", 0),
    ],
)
def test_fetch_orders_parses_formatted_money_string_to_paise(formatted, expected_paise):
    client, _ = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [_raw_order("o1", orderTotal=formatted)]},
        details_by_order_id={"o1": _detail("o1")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert orders[0].grand_total_paise == expected_paise


def test_fetch_orders_sets_vendor_name_from_restaurant_name():
    client, _ = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [_raw_order("o1", restaurantName="Biryani House")]},
        details_by_order_id={"o1": _detail("o1")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert orders[0].vendor_name == "Biryani House"


@pytest.mark.parametrize(
    "status,expected_is_cancelled",
    [("DELIVERED", False), ("CANCELLED", True), ("cancelled", True)],
)
def test_fetch_orders_sets_is_cancelled_from_order_status(status, expected_is_cancelled):
    client, _ = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [_raw_order("o1", orderStatus=status)]},
        details_by_order_id={"o1": _detail("o1")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert orders[0].is_cancelled is expected_is_cancelled
    assert orders[0].status == status


def test_fetch_orders_keeps_raw_payload_and_never_recomputes_grand_total():
    raw = _raw_order("o1", orderTotal="₹273")
    client, _ = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [raw]},
        details_by_order_id={"o1": _detail("o1")},
    )

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert orders[0].raw == raw
    assert orders[0].grand_total_paise == 27300

    source = inspect.getsource(swiggy_food)
    for lineno, line in enumerate(source.splitlines(), start=1):
        if "paise" not in line.lower() and "grand_total" not in line.lower():
            continue
        assert "+" not in line, f"line {lineno} performs arithmetic on the total: {line!r}"
        assert "sum(" not in line, f"line {lineno} performs arithmetic on the total: {line!r}"


def test_fetch_orders_only_calls_read_only_tool_names():
    client, calls = _make_client(
        addresses=[{"id": "addr1"}],
        orders_by_address={"addr1": [_raw_order("o1")]},
        details_by_order_id={"o1": _detail("o1")},
    )

    fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    tool_names_called = {call[2] for call in calls}
    assert tool_names_called == {"get_addresses", "get_food_orders", "get_food_order_details"}


def test_fetch_orders_returns_empty_list_when_no_addresses():
    client, calls = _make_client(addresses=[], orders_by_address={}, details_by_order_id={})

    orders = fetch_orders(client, _BASE_URL, _ACCESS_TOKEN)

    assert orders == []
    assert calls == [(_BASE_URL, _ACCESS_TOKEN, "get_addresses", {})]
