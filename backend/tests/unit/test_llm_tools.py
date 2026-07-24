import httpx
import pytest

from app.core import db
from app.core.config import Settings, get_settings
from app.llm import tools


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
    settings = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="anon-key")
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


def _order(**overrides):
    row = {
        "id": "o0",
        "platform": "swiggy_food",
        "is_cancelled": False,
        "grand_total_paise": 0,
        "ordered_at": "2026-05-15T12:00:00Z",
    }
    row.update(overrides)
    return row


def _item(**overrides):
    row = {
        "order_id": "o0",
        "name": "item",
        "quantity": 1,
        "unit_price_paise": 0,
        "category": None,
    }
    row.update(overrides)
    return row


def _fake_postgrest(orders: list[dict], order_items: list[dict]) -> httpx.MockTransport:
    """Emulates just enough of PostgREST's filter semantics (`eq.`/`in.()`) for
    these tests: real filtering happens server-side, so a transport-level fake
    has to apply it, or every "filters narrow results" assertion would pass
    trivially regardless of whether `tools.py` sent the right filter.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/v1/orders":
            rows = orders
            if request.url.params.get("is_cancelled") == "eq.false":
                rows = [row for row in rows if not row.get("is_cancelled", False)]
            platform_filter = request.url.params.get("platform")
            if platform_filter is not None:
                rows = [row for row in rows if f"eq.{row['platform']}" == platform_filter]
            return httpx.Response(200, json=rows)
        if request.url.path == "/rest/v1/order_items":
            order_id_filter = request.url.params.get("order_id", "in.()")
            ids = set(order_id_filter.removeprefix("in.(").removesuffix(")").split(","))
            rows = [item for item in order_items if item["order_id"] in ids]
            return httpx.Response(200, json=rows)
        raise AssertionError(f"unexpected request path: {request.url.path}")

    return httpx.MockTransport(handler)


# --- spend_total ------------------------------------------------------------


def test_spend_total_sums_paise_and_counts_orders_in_range():
    orders = [
        _order(id="o1", grand_total_paise=50000, ordered_at="2026-05-01T00:00:00Z"),
        _order(id="o2", grand_total_paise=30000, ordered_at="2026-05-20T00:00:00Z"),
        _order(id="o3", grand_total_paise=20000, ordered_at="2026-06-01T00:00:00Z"),
    ]
    transport = _fake_postgrest(orders, [])

    result = tools.spend_total("jwt", "2026-05-01", "2026-05-31", transport=transport)

    assert result == {"total_paise": 80000, "order_count": 2}


def test_spend_total_excludes_cancelled_orders_by_default():
    orders = [
        _order(id="o1", grand_total_paise=50000, is_cancelled=False),
        _order(id="o2", grand_total_paise=99999, is_cancelled=True),
    ]
    transport = _fake_postgrest(orders, [])

    result = tools.spend_total("jwt", "2026-05-01", "2026-05-31", transport=transport)

    assert result == {"total_paise": 50000, "order_count": 1}


def test_spend_total_platform_filter_narrows_results():
    orders = [
        _order(id="o1", platform="zepto", grand_total_paise=10000),
        _order(id="o2", platform="swiggy_food", grand_total_paise=20000),
    ]
    transport = _fake_postgrest(orders, [])

    result = tools.spend_total(
        "jwt", "2026-05-01", "2026-05-31", platform="zepto", transport=transport
    )

    assert result == {"total_paise": 10000, "order_count": 1}


def test_spend_total_category_filter_narrows_to_orders_with_matching_item():
    orders = [
        _order(id="o1", grand_total_paise=10000),
        _order(id="o2", grand_total_paise=20000),
    ]
    items = [
        _item(order_id="o1", category="grocery"),
        _item(order_id="o2", category="snacks"),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.spend_total(
        "jwt", "2026-05-01", "2026-05-31", category="grocery", transport=transport
    )

    assert result == {"total_paise": 10000, "order_count": 1}


def test_spend_total_item_name_contains_is_case_insensitive_substring():
    orders = [
        _order(id="o1", grand_total_paise=10000),
        _order(id="o2", grand_total_paise=20000),
    ]
    items = [
        _item(order_id="o1", name="Amul Milk 1L"),
        _item(order_id="o2", name="Bread"),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.spend_total(
        "jwt", "2026-05-01", "2026-05-31", item_name_contains="milk", transport=transport
    )

    assert result == {"total_paise": 10000, "order_count": 1}


def test_spend_total_returns_zero_for_no_matching_orders():
    transport = _fake_postgrest([], [])

    result = tools.spend_total("jwt", "2026-05-01", "2026-05-31", transport=transport)

    assert result == {"total_paise": 0, "order_count": 0}


# --- spend_by_category --------------------------------------------------


def test_spend_by_category_sums_item_components_grouped_by_category():
    orders = [_order(id="o1"), _order(id="o2")]
    items = [
        _item(order_id="o1", category="grocery", quantity=2, unit_price_paise=5000),
        _item(order_id="o2", category="grocery", quantity=1, unit_price_paise=3000),
        _item(order_id="o2", category="snacks", quantity=1, unit_price_paise=2000),
        _item(order_id="o2", category=None, quantity=1, unit_price_paise=9999),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.spend_by_category("jwt", "2026-05-01", "2026-05-31", transport=transport)

    assert result == [
        {"category": "grocery", "total_paise": 13000},
        {"category": "snacks", "total_paise": 2000},
    ]


# --- spend_by_platform --------------------------------------------------


def test_spend_by_platform_sums_grand_total_ranked_descending():
    orders = [
        _order(id="o1", platform="zepto", grand_total_paise=10000),
        _order(id="o2", platform="swiggy_food", grand_total_paise=50000),
        _order(id="o3", platform="zepto", grand_total_paise=5000),
    ]
    transport = _fake_postgrest(orders, [])

    result = tools.spend_by_platform("jwt", "2026-05-01", "2026-05-31", transport=transport)

    assert result == [
        {"platform": "swiggy_food", "total_paise": 50000},
        {"platform": "zepto", "total_paise": 15000},
    ]


# --- top_items -----------------------------------------------------------


def test_top_items_ranks_by_spend_and_counts_distinct_orders():
    orders = [_order(id="o1"), _order(id="o2"), _order(id="o3")]
    items = [
        _item(order_id="o1", name="Milk", quantity=2, unit_price_paise=3000),
        _item(order_id="o2", name="Milk", quantity=1, unit_price_paise=3000),
        _item(order_id="o2", name="Milk", quantity=1, unit_price_paise=3000),
        _item(order_id="o3", name="Bread", quantity=1, unit_price_paise=4000),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.top_items("jwt", "2026-05-01", "2026-05-31", transport=transport)

    assert result == [
        {"name": "Milk", "order_count": 2, "total_paise": 12000},
        {"name": "Bread", "order_count": 1, "total_paise": 4000},
    ]


def test_top_items_respects_limit():
    orders = [_order(id="o1"), _order(id="o2")]
    items = [
        _item(order_id="o1", name="Milk", quantity=1, unit_price_paise=3000),
        _item(order_id="o2", name="Bread", quantity=1, unit_price_paise=4000),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.top_items("jwt", "2026-05-01", "2026-05-31", limit=1, transport=transport)

    assert len(result) == 1
    assert result[0]["name"] == "Bread"


# --- top_items_in_category --------------------------------------------------


def test_top_items_in_category_narrows_to_matching_category():
    orders = [_order(id="o1"), _order(id="o2"), _order(id="o3")]
    items = [
        _item(order_id="o1", name="Milk", quantity=2, unit_price_paise=3000, category="grocery"),
        _item(order_id="o2", name="Milk", quantity=1, unit_price_paise=3000, category="grocery"),
        _item(order_id="o3", name="Pizza", quantity=1, unit_price_paise=9000, category="food"),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.top_items_in_category(
        "jwt", "2026-05-01", "2026-05-31", "grocery", transport=transport
    )

    assert result == [{"name": "Milk", "order_count": 2, "total_paise": 9000}]


def test_top_items_in_category_with_no_category_behaves_like_top_items():
    orders = [_order(id="o1"), _order(id="o2")]
    items = [
        _item(order_id="o1", name="Milk", quantity=1, unit_price_paise=3000, category="grocery"),
        _item(order_id="o2", name="Pizza", quantity=1, unit_price_paise=9000, category="food"),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.top_items_in_category("jwt", "2026-05-01", "2026-05-31", transport=transport)

    assert result == tools.top_items("jwt", "2026-05-01", "2026-05-31", transport=transport)


def test_top_items_in_category_returns_empty_for_uncategorized_data():
    orders = [_order(id="o1")]
    items = [_item(order_id="o1", name="Milk", quantity=1, unit_price_paise=3000, category=None)]
    transport = _fake_postgrest(orders, items)

    result = tools.top_items_in_category(
        "jwt", "2026-05-01", "2026-05-31", "grocery", transport=transport
    )

    assert result == []


def test_top_items_in_category_respects_limit():
    orders = [_order(id="o1"), _order(id="o2")]
    items = [
        _item(order_id="o1", name="Milk", quantity=1, unit_price_paise=3000, category="grocery"),
        _item(order_id="o2", name="Bread", quantity=1, unit_price_paise=4000, category="grocery"),
    ]
    transport = _fake_postgrest(orders, items)

    result = tools.top_items_in_category(
        "jwt", "2026-05-01", "2026-05-31", "grocery", limit=1, transport=transport
    )

    assert len(result) == 1
    assert result[0]["name"] == "Bread"


# --- data_coverage ---------------------------------------------------------


def test_data_coverage_reflects_min_max_ordered_at_and_synced_platforms():
    orders = [
        _order(id="o1", platform="zepto", ordered_at="2026-04-10T00:00:00Z"),
        _order(id="o2", platform="swiggy_food", ordered_at="2026-06-01T00:00:00Z"),
        _order(id="o3", platform="zepto", ordered_at="2026-05-01T00:00:00Z"),
    ]
    transport = _fake_postgrest(orders, [])

    result = tools.data_coverage("jwt", transport=transport)

    assert result == {
        "earliest_order_at": "2026-04-10T00:00:00Z",
        "latest_order_at": "2026-06-01T00:00:00Z",
        "platforms_synced": ["swiggy_food", "zepto"],
    }


def test_data_coverage_excludes_cancelled_orders():
    orders = [_order(id="o1", is_cancelled=True, platform="zepto")]
    transport = _fake_postgrest(orders, [])

    result = tools.data_coverage("jwt", transport=transport)

    assert result == {"earliest_order_at": None, "latest_order_at": None, "platforms_synced": []}


def test_data_coverage_with_no_synced_orders_returns_nulls():
    transport = _fake_postgrest([], [])

    result = tools.data_coverage("jwt", transport=transport)

    assert result == {"earliest_order_at": None, "latest_order_at": None, "platforms_synced": []}
