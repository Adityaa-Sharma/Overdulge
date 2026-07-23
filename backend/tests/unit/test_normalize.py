import inspect
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.core import db
from app.core.config import Settings, get_settings
from app.sync import normalize
from app.sync.normalize import NormalizedOrder, NormalizedOrderItem, upsert_orders


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
    settings = Settings(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        supabase_service_role_key="service-role-key",
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


def _client_returning_order_ids(captured: list[httpx.Request]) -> db.PostgrestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/orders") and request.method == "POST":
            body = json.loads(request.content)
            rows = [{**row, "id": f"id-{row['platform_order_id']}"} for row in body]
            return httpx.Response(200, json=rows)
        if request.url.path.endswith("/order_items"):
            if request.method == "DELETE":
                return httpx.Response(200, json=[])
            body = json.loads(request.content)
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=[])

    return db.service_role_client(transport=httpx.MockTransport(handler))


def _order(**overrides) -> NormalizedOrder:
    fields = {
        "platform_order_id": "p1",
        "status": "DELIVERED",
        "is_cancelled": False,
        "ordered_at": datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        "grand_total_paise": 27300,
        "raw": {"grandTotal": "₹273"},
    }
    fields.update(overrides)
    return NormalizedOrder(**fields)


def test_upsert_orders_builds_order_row_and_upserts_with_on_conflict():
    captured: list[httpx.Request] = []
    client = _client_returning_order_ids(captured)
    order = _order(vendor_name="Biryani House", address_id="addr1", address_label="Home")

    result = upsert_orders(client, user_id="u1", platform="swiggy_food", orders=[order])

    assert result.orders_upserted == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/orders")
    assert request.url.params["on_conflict"] == "platform,platform_order_id"
    body = json.loads(request.content)
    assert body == [
        {
            "user_id": "u1",
            "platform": "swiggy_food",
            "vendor_name": "Biryani House",
            "platform_order_id": "p1",
            "address_id": "addr1",
            "address_label": "Home",
            "status": "DELIVERED",
            "is_cancelled": False,
            "ordered_at": "2026-07-20T12:00:00+00:00",
            "grand_total_paise": 27300,
            "item_total_paise": None,
            "fees_paise": None,
            "raw": {"grandTotal": "₹273"},
        }
    ]


def test_upsert_orders_shapes_order_items_with_returned_order_id():
    captured: list[httpx.Request] = []
    client = _client_returning_order_ids(captured)
    order = _order(
        items=[
            NormalizedOrderItem(name="Biryani", quantity=1, unit_price_paise=20000),
            NormalizedOrderItem(name="Raita", quantity=2, unit_price_paise=5000),
        ]
    )

    result = upsert_orders(client, user_id="u1", platform="swiggy_food", orders=[order])

    assert result.order_items_upserted == 2
    delete_request, insert_request = captured[1], captured[2]
    assert delete_request.method == "DELETE"
    assert delete_request.url.path.endswith("/order_items")
    assert delete_request.url.params["order_id"] == "eq.id-p1"
    assert insert_request.method == "POST"
    assert insert_request.url.path.endswith("/order_items")
    body = json.loads(insert_request.content)
    assert body == [
        {
            "order_id": "id-p1",
            "name": "Biryani",
            "quantity": 1,
            "unit_price_paise": 20000,
            "platform_item_id": None,
            "product_variant_id": None,
            "category": None,
            "is_veg": None,
            "calorie_estimate": None,
        },
        {
            "order_id": "id-p1",
            "name": "Raita",
            "quantity": 2,
            "unit_price_paise": 5000,
            "platform_item_id": None,
            "product_variant_id": None,
            "category": None,
            "is_veg": None,
            "calorie_estimate": None,
        },
    ]


def test_upsert_orders_clears_stale_items_but_skips_insert_when_order_has_no_items():
    # The delete still runs (a re-sync where an order's item list shrank to
    # empty must not leave stale rows behind); only the insert is skipped.
    captured: list[httpx.Request] = []
    client = _client_returning_order_ids(captured)

    result = upsert_orders(client, user_id="u1", platform="zepto", orders=[_order()])

    assert result.order_items_upserted == 0
    order_item_requests = [r for r in captured if r.url.path.endswith("/order_items")]
    assert len(order_item_requests) == 1
    assert order_item_requests[0].method == "DELETE"


def test_upsert_orders_grand_total_paise_passed_through_verbatim_never_recomputed():
    captured: list[httpx.Request] = []
    client = _client_returning_order_ids(captured)
    # Item components deliberately don't sum to grand_total_paise (unitemized
    # discounts, BRD §2.8) — the stored total must still equal the input exactly.
    order = _order(
        grand_total_paise=27300,
        item_total_paise=30000,
        fees_paise=1000,
        items=[NormalizedOrderItem(name="Biryani", quantity=1, unit_price_paise=30000)],
    )

    upsert_orders(client, user_id="u1", platform="swiggy_food", orders=[order])

    body = json.loads(captured[0].content)
    assert body[0]["grand_total_paise"] == 27300
    assert body[0]["item_total_paise"] == 30000
    assert body[0]["fees_paise"] == 1000


def test_upsert_orders_rerun_with_same_input_uses_on_conflict_both_times():
    captured: list[httpx.Request] = []
    client = _client_returning_order_ids(captured)
    order = _order()

    upsert_orders(client, user_id="u1", platform="zepto", orders=[order])
    upsert_orders(client, user_id="u1", platform="zepto", orders=[order])

    order_post_requests = [
        r for r in captured if r.method == "POST" and r.url.path.endswith("/orders")
    ]
    assert len(order_post_requests) == 2
    for request in order_post_requests:
        assert request.url.params["on_conflict"] == "platform,platform_order_id"


def test_upsert_orders_empty_input_makes_no_requests():
    captured: list[httpx.Request] = []
    client = _client_returning_order_ids(captured)

    result = upsert_orders(client, user_id="u1", platform="zepto", orders=[])

    assert result.orders_upserted == 0
    assert result.order_items_upserted == 0
    assert captured == []


def test_normalize_module_never_performs_arithmetic_on_paise_fields():
    # BRD §2.8 / AC-7: grand_total_paise (and its sibling *_paise fields) must
    # never be derived by summing components anywhere in this module.
    source = inspect.getsource(normalize)
    for lineno, line in enumerate(source.splitlines(), start=1):
        if "paise" not in line:
            continue
        assert "+" not in line, f"line {lineno} performs arithmetic on a *_paise field: {line!r}"
        assert "sum(" not in line, f"line {lineno} performs arithmetic on a *_paise field: {line!r}"
