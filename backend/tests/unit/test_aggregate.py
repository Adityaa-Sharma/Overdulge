from datetime import UTC, datetime

import pytest

from app.analytics import aggregate

NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC)  # Thursday, week starts 2026-07-20


def _order(**overrides):
    row = {
        "id": "o0",
        "platform": "swiggy_food",
        "vendor_name": None,
        "address_id": None,
        "grand_total_paise": 0,
        "ordered_at": NOW.isoformat(),
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


def _budget(**overrides):
    row = {"category": None, "cap_paise": 100}
    row.update(overrides)
    return row


# --- spend_totals ---------------------------------------------------------


def test_spend_totals_sums_combined_across_platforms_without_double_counting():
    orders = [
        _order(
            id="o1",
            platform="swiggy_food",
            grand_total_paise=50000,
            ordered_at="2026-07-21T12:00:00Z",
        ),
        _order(
            id="o2",
            platform="swiggy_instamart",
            grand_total_paise=30000,
            ordered_at="2026-07-22T09:00:00Z",
        ),
        _order(
            id="o3", platform="zepto", grand_total_paise=20000, ordered_at="2026-07-01T00:00:00Z"
        ),
        _order(
            id="o4",
            platform="swiggy_food",
            grand_total_paise=99999,
            ordered_at="2026-06-15T00:00:00Z",
        ),
    ]

    result = aggregate.spend_totals(orders, now=NOW)

    assert result["this_week_paise"] == {
        "combined": 80000,
        "swiggy_food": 50000,
        "swiggy_instamart": 30000,
        "zepto": 0,
    }
    assert result["this_month_paise"] == {
        "combined": 100000,
        "swiggy_food": 50000,
        "swiggy_instamart": 30000,
        "zepto": 20000,
    }


def test_spend_totals_empty_input_is_all_zero():
    result = aggregate.spend_totals([], now=NOW)

    zeroed = {"combined": 0, "swiggy_food": 0, "swiggy_instamart": 0, "zepto": 0}
    assert result == {"this_week_paise": zeroed, "this_month_paise": zeroed}


# --- spend_trend -----------------------------------------------------------


def test_spend_trend_weekly_buckets_oldest_first():
    orders = [
        _order(
            id="o1",
            platform="swiggy_food",
            grand_total_paise=50000,
            ordered_at="2026-07-21T12:00:00Z",
        ),
        _order(
            id="o2",
            platform="swiggy_instamart",
            grand_total_paise=30000,
            ordered_at="2026-07-22T09:00:00Z",
        ),
    ]

    result = aggregate.spend_trend(orders, bucket="week", now=NOW, lookback=2)

    assert result == [
        {
            "period_start": "2026-07-13",
            "combined_paise": 0,
            "swiggy_food_paise": 0,
            "swiggy_instamart_paise": 0,
            "zepto_paise": 0,
        },
        {
            "period_start": "2026-07-20",
            "combined_paise": 80000,
            "swiggy_food_paise": 50000,
            "swiggy_instamart_paise": 30000,
            "zepto_paise": 0,
        },
    ]


def test_spend_trend_monthly_buckets_oldest_first():
    orders = [
        _order(
            id="o1",
            platform="swiggy_food",
            grand_total_paise=50000,
            ordered_at="2026-07-21T12:00:00Z",
        ),
        _order(
            id="o2",
            platform="swiggy_instamart",
            grand_total_paise=30000,
            ordered_at="2026-07-22T09:00:00Z",
        ),
        _order(
            id="o3", platform="zepto", grand_total_paise=20000, ordered_at="2026-07-01T00:00:00Z"
        ),
        _order(
            id="o4",
            platform="swiggy_food",
            grand_total_paise=99999,
            ordered_at="2026-06-15T00:00:00Z",
        ),
    ]

    result = aggregate.spend_trend(orders, bucket="month", now=NOW, lookback=2)

    assert result == [
        {
            "period_start": "2026-06-01",
            "combined_paise": 99999,
            "swiggy_food_paise": 99999,
            "swiggy_instamart_paise": 0,
            "zepto_paise": 0,
        },
        {
            "period_start": "2026-07-01",
            "combined_paise": 100000,
            "swiggy_food_paise": 50000,
            "swiggy_instamart_paise": 30000,
            "zepto_paise": 20000,
        },
    ]


def test_spend_trend_empty_input_returns_zeroed_buckets():
    result = aggregate.spend_trend([], bucket="week", now=NOW, lookback=3)

    assert [row["period_start"] for row in result] == ["2026-07-06", "2026-07-13", "2026-07-20"]
    assert all(
        row["combined_paise"] == 0
        and row["swiggy_food_paise"] == 0
        and row["swiggy_instamart_paise"] == 0
        and row["zepto_paise"] == 0
        for row in result
    )


def test_spend_trend_rejects_unknown_bucket():
    with pytest.raises(ValueError):
        aggregate.spend_trend([], bucket="day", now=NOW)


# --- category_breakdown -----------------------------------------------------


def test_category_breakdown_splits_food_vs_grocery_and_groups_items():
    orders = [
        _order(id="o1", platform="swiggy_food", grand_total_paise=1000),
        _order(id="o2", platform="swiggy_instamart", grand_total_paise=2000),
        _order(id="o3", platform="zepto", grand_total_paise=1500),
    ]
    order_items = [
        _item(order_id="o1", category="snacks", quantity=2, unit_price_paise=100),
        _item(order_id="o2", category="snacks", quantity=1, unit_price_paise=300),
        _item(order_id="o3", category=None, quantity=5, unit_price_paise=50),
        _item(order_id="o1", category="beverages", quantity=3, unit_price_paise=50),
    ]

    result = aggregate.category_breakdown(orders, order_items)

    assert result == {
        "food_delivery_paise": 1000,
        "grocery_paise": 3500,
        "item_categories_paise": {"snacks": 500, "beverages": 150},
    }


def test_category_breakdown_empty_input_is_all_zero():
    result = aggregate.category_breakdown([], [])

    assert result == {
        "food_delivery_paise": 0,
        "grocery_paise": 0,
        "item_categories_paise": {},
    }


# --- top_restaurants ---------------------------------------------------------


def test_top_restaurants_groups_by_vendor_excludes_null_and_other_platforms():
    orders = [
        _order(
            id="o1", platform="swiggy_food", vendor_name="Biryani House", grand_total_paise=50000
        ),
        _order(
            id="o2", platform="swiggy_food", vendor_name="Biryani House", grand_total_paise=30000
        ),
        _order(id="o3", platform="swiggy_food", vendor_name="Pizza Place", grand_total_paise=40000),
        _order(id="o4", platform="swiggy_food", vendor_name=None, grand_total_paise=10000),
        _order(
            id="o5",
            platform="swiggy_instamart",
            vendor_name="Should not appear",
            grand_total_paise=99999,
        ),
    ]

    result = aggregate.top_restaurants(orders)

    assert result == [
        {"name": "Biryani House", "spend_paise": 80000, "order_count": 2},
        {"name": "Pizza Place", "spend_paise": 40000, "order_count": 1},
    ]


def test_top_restaurants_respects_limit():
    orders = [
        _order(
            id="o1", platform="swiggy_food", vendor_name="Biryani House", grand_total_paise=50000
        ),
        _order(id="o2", platform="swiggy_food", vendor_name="Pizza Place", grand_total_paise=40000),
    ]

    result = aggregate.top_restaurants(orders, limit=1)

    assert result == [{"name": "Biryani House", "spend_paise": 50000, "order_count": 1}]


def test_top_restaurants_empty_input_returns_empty_list():
    assert aggregate.top_restaurants([]) == []


# --- top_products -------------------------------------------------------------


def test_top_products_groups_instamart_and_zepto_excludes_food():
    orders = [
        _order(id="o1", platform="swiggy_instamart"),
        _order(id="o2", platform="zepto"),
        _order(id="o3", platform="swiggy_food"),
    ]
    order_items = [
        _item(order_id="o1", name="Milk", quantity=2, unit_price_paise=50),
        _item(order_id="o2", name="Milk", quantity=1, unit_price_paise=60),
        _item(order_id="o1", name="Bread", quantity=1, unit_price_paise=40),
        _item(order_id="o3", name="Biryani", quantity=1, unit_price_paise=30000),
    ]

    result = aggregate.top_products(orders, order_items)

    assert result == [
        {"name": "Milk", "spend_paise": 160, "order_count": 2},
        {"name": "Bread", "spend_paise": 40, "order_count": 1},
    ]


def test_top_products_empty_input_returns_empty_list():
    assert aggregate.top_products([], []) == []


# --- order_stats ---------------------------------------------------------------


def test_order_stats_computes_count_and_average_per_platform():
    orders = [
        _order(id="o1", platform="swiggy_food", grand_total_paise=1000),
        _order(id="o2", platform="swiggy_food", grand_total_paise=3000),
        _order(id="o3", platform="zepto", grand_total_paise=999),
    ]

    result = aggregate.order_stats(orders)

    assert result == {
        "swiggy_food": {"order_count": 2, "avg_order_value_paise": 2000},
        "swiggy_instamart": {"order_count": 0, "avg_order_value_paise": None},
        "zepto": {"order_count": 1, "avg_order_value_paise": 999},
    }


def test_order_stats_empty_input_has_null_average_not_zero():
    result = aggregate.order_stats([])

    assert result == {
        "swiggy_food": {"order_count": 0, "avg_order_value_paise": None},
        "swiggy_instamart": {"order_count": 0, "avg_order_value_paise": None},
        "zepto": {"order_count": 0, "avg_order_value_paise": None},
    }


# --- spend_projection ------------------------------------------------------------


def test_spend_projection_computes_run_rate():
    orders = [
        _order(platform="swiggy_food", grand_total_paise=3100, ordered_at="2026-07-05T00:00:00Z"),
        _order(
            platform="swiggy_instamart", grand_total_paise=2000, ordered_at="2026-07-10T00:00:00Z"
        ),
        _order(platform="zepto", grand_total_paise=900, ordered_at="2026-07-15T00:00:00Z"),
        _order(platform="swiggy_food", grand_total_paise=99999, ordered_at="2026-06-15T00:00:00Z"),
    ]

    result = aggregate.spend_projection(orders, now=NOW)

    assert result["month"] == "2026-07"
    assert result["days_elapsed"] == 22
    assert result["days_in_month"] == 31
    assert result["label"] == "Projection"
    assert result["spend_to_date_paise"] == {
        "combined": 6000,
        "swiggy_food": 3100,
        "swiggy_instamart": 2000,
        "zepto": 900,
    }
    assert result["projected_total_paise"] == {
        "combined": 8455,
        "swiggy_food": 4368,
        "swiggy_instamart": 2818,
        "zepto": 1268,
    }


def test_spend_projection_guards_day_one_divide_by_zero():
    day_one = datetime(2026, 7, 1, 5, 30, 0, tzinfo=UTC)
    orders = [
        _order(platform="swiggy_food", grand_total_paise=500, ordered_at="2026-07-01T01:00:00Z"),
    ]

    result = aggregate.spend_projection(orders, now=day_one)

    assert result["days_elapsed"] == 0
    assert result["spend_to_date_paise"]["combined"] == 500
    assert result["projected_total_paise"] == {
        "combined": 0,
        "swiggy_food": 0,
        "swiggy_instamart": 0,
        "zepto": 0,
    }


def test_spend_projection_empty_input_is_all_zero():
    result = aggregate.spend_projection([], now=NOW)

    assert result["spend_to_date_paise"] == {
        "combined": 0,
        "swiggy_food": 0,
        "swiggy_instamart": 0,
        "zepto": 0,
    }
    assert result["projected_total_paise"] == {
        "combined": 0,
        "swiggy_food": 0,
        "swiggy_instamart": 0,
        "zepto": 0,
    }


# --- location_lens -----------------------------------------------------------------


def test_location_lens_groups_by_address_excludes_null_and_other_platforms():
    orders = [
        _order(
            id="o1",
            platform="swiggy_food",
            address_id="addr1",
            address_label="Home",
            grand_total_paise=1000,
        ),
        _order(
            id="o2",
            platform="swiggy_food",
            address_id="addr1",
            address_label="Home",
            grand_total_paise=500,
        ),
        _order(
            id="o3",
            platform="swiggy_food",
            address_id="addr2",
            address_label="Work",
            grand_total_paise=2000,
        ),
        _order(id="o4", platform="swiggy_food", address_id=None, grand_total_paise=300),
        _order(id="o5", platform="swiggy_instamart", address_id="addr3", grand_total_paise=9999),
    ]

    result = aggregate.location_lens(orders)

    assert result == [
        {"address_id": "addr2", "address_label": "Work", "spend_paise": 2000, "order_count": 1},
        {"address_id": "addr1", "address_label": "Home", "spend_paise": 1500, "order_count": 2},
    ]


def test_location_lens_label_is_none_when_no_order_carried_one():
    # Older rows synced before labels existed still group; the label is null.
    orders = [
        _order(id="o1", platform="swiggy_food", address_id="addr1", grand_total_paise=1000),
    ]

    result = aggregate.location_lens(orders)

    assert result == [
        {"address_id": "addr1", "address_label": None, "spend_paise": 1000, "order_count": 1},
    ]


def test_location_lens_empty_input_returns_empty_list():
    assert aggregate.location_lens([]) == []


# --- budget_progress -----------------------------------------------------------------

_BUDGET_ORDERS = [
    _order(
        id="o1", platform="swiggy_food", grand_total_paise=50000, ordered_at="2026-07-21T12:00:00Z"
    ),
    _order(
        id="o2",
        platform="swiggy_instamart",
        grand_total_paise=30000,
        ordered_at="2026-07-22T09:00:00Z",
    ),
    _order(id="o3", platform="zepto", grand_total_paise=20000, ordered_at="2026-06-15T00:00:00Z"),
]
_BUDGET_ITEMS = [
    _item(order_id="o1", category="snacks", quantity=2, unit_price_paise=100),
    _item(order_id="o2", category="snacks", quantity=1, unit_price_paise=300),
    _item(order_id="o3", category="snacks", quantity=5, unit_price_paise=1000),
    _item(order_id="o1", category="beverages", quantity=3, unit_price_paise=50),
]


def test_budget_progress_computes_overall_and_category_caps_for_current_month():
    budgets = [
        _budget(category=None, cap_paise=100000),
        _budget(category="beverages", cap_paise=1000),
    ]

    result = aggregate.budget_progress(_BUDGET_ORDERS, _BUDGET_ITEMS, budgets, now=NOW)

    assert result == [
        {"category": None, "cap_paise": 100000, "spent_paise": 80000, "pct": 0.8, "status": "near"},
        {
            "category": "beverages",
            "cap_paise": 1000,
            "spent_paise": 150,
            "pct": 0.15,
            "status": "ok",
        },
    ]


def test_budget_progress_category_with_no_matching_spend_is_zero_and_ok():
    budgets = [_budget(category="produce", cap_paise=200)]

    result = aggregate.budget_progress(_BUDGET_ORDERS, _BUDGET_ITEMS, budgets, now=NOW)

    assert result == [
        {"category": "produce", "cap_paise": 200, "spent_paise": 0, "pct": 0.0, "status": "ok"}
    ]


def test_budget_progress_status_boundaries_at_exactly_80_and_100_percent():
    budgets = [
        _budget(category=None, cap_paise=100000),  # spent 80000 -> exactly 80%
        _budget(category="snacks", cap_paise=500),  # spent 500 -> exactly 100%
    ]

    result = aggregate.budget_progress(_BUDGET_ORDERS, _BUDGET_ITEMS, budgets, now=NOW)

    assert result[0]["pct"] == 0.8
    assert result[0]["status"] == "near"
    assert result[1]["pct"] == 1.0
    assert result[1]["status"] == "over"


def test_budget_progress_multiple_caps_each_get_independent_rows():
    budgets = [
        _budget(category=None, cap_paise=100000),
        _budget(category="snacks", cap_paise=500),
        _budget(category="beverages", cap_paise=1000),
        _budget(category="produce", cap_paise=200),
    ]

    result = aggregate.budget_progress(_BUDGET_ORDERS, _BUDGET_ITEMS, budgets, now=NOW)

    assert [row["category"] for row in result] == [None, "snacks", "beverages", "produce"]
    assert [row["status"] for row in result] == ["near", "over", "ok", "ok"]


def test_budget_progress_empty_budgets_returns_empty_list():
    assert aggregate.budget_progress(_BUDGET_ORDERS, _BUDGET_ITEMS, [], now=NOW) == []


# --- calorie_totals -----------------------------------------------------------------

_CALORIE_ORDERS = [
    _order(id="o1", ordered_at="2026-07-21T12:00:00Z"),  # this week
    _order(id="o2", ordered_at="2026-07-22T09:00:00Z"),  # this week
    _order(id="o3", ordered_at="2026-07-05T00:00:00Z"),  # this month, not this week
    _order(id="o4", ordered_at="2026-06-15T00:00:00Z"),  # neither
]


def test_calorie_totals_sums_present_estimates_within_week_and_month():
    order_items = [
        _item(order_id="o1", calorie_estimate=300),
        _item(order_id="o2", calorie_estimate=150),
        _item(order_id="o3", calorie_estimate=400),
        _item(order_id="o4", calorie_estimate=999),
    ]

    result = aggregate.calorie_totals(_CALORIE_ORDERS, order_items, now=NOW)

    assert result == {"this_week_estimate_kcal": 450, "this_month_estimate_kcal": 850}


def test_calorie_totals_skips_items_with_none_estimate():
    order_items = [
        _item(order_id="o1", calorie_estimate=300),
        _item(order_id="o1", calorie_estimate=None),
        _item(order_id="o2", calorie_estimate=None),
    ]

    result = aggregate.calorie_totals(_CALORIE_ORDERS, order_items, now=NOW)

    assert result == {"this_week_estimate_kcal": 300, "this_month_estimate_kcal": 300}


def test_calorie_totals_skips_items_missing_estimate_key_entirely():
    order_items = [
        _item(order_id="o1", calorie_estimate=300),
        _item(order_id="o1"),  # no calorie_estimate key at all, e.g. not yet estimated
    ]

    result = aggregate.calorie_totals(_CALORIE_ORDERS, order_items, now=NOW)

    assert result == {"this_week_estimate_kcal": 300, "this_month_estimate_kcal": 300}


def test_calorie_totals_sums_multiple_items_per_order():
    order_items = [
        _item(order_id="o1", calorie_estimate=300),
        _item(order_id="o1", calorie_estimate=200),
    ]

    result = aggregate.calorie_totals(_CALORIE_ORDERS, order_items, now=NOW)

    assert result == {"this_week_estimate_kcal": 500, "this_month_estimate_kcal": 500}


def test_calorie_totals_empty_input_is_all_zero():
    result = aggregate.calorie_totals([], [], now=NOW)

    assert result == {"this_week_estimate_kcal": 0, "this_month_estimate_kcal": 0}


# --- calorie_trend -------------------------------------------------------------------


def test_calorie_trend_weekly_buckets_zero_fill_and_oldest_first():
    orders = [
        _order(id="o1", ordered_at="2026-07-21T12:00:00Z"),  # week of 2026-07-20
        _order(id="o2", ordered_at="2026-07-22T09:00:00Z"),  # week of 2026-07-20
    ]
    order_items = [
        _item(order_id="o1", calorie_estimate=300),
        _item(order_id="o2", calorie_estimate=150),
    ]

    result = aggregate.calorie_trend(orders, order_items, now=NOW, lookback=2)

    assert result == [
        {"period_start": "2026-07-13", "estimate_kcal": 0},
        {"period_start": "2026-07-20", "estimate_kcal": 450},
    ]


def test_calorie_trend_skips_items_with_none_estimate():
    orders = [_order(id="o1", ordered_at="2026-07-21T12:00:00Z")]
    order_items = [
        _item(order_id="o1", calorie_estimate=300),
        _item(order_id="o1", calorie_estimate=None),
    ]

    result = aggregate.calorie_trend(orders, order_items, now=NOW, lookback=1)

    assert result == [{"period_start": "2026-07-20", "estimate_kcal": 300}]


def test_calorie_trend_orders_outside_lookback_window_are_dropped():
    orders = [_order(id="o1", ordered_at="2026-01-01T00:00:00Z")]
    order_items = [_item(order_id="o1", calorie_estimate=500)]

    result = aggregate.calorie_trend(orders, order_items, now=NOW, lookback=1)

    assert result == [{"period_start": "2026-07-20", "estimate_kcal": 0}]


def test_calorie_trend_empty_input_returns_zeroed_buckets():
    result = aggregate.calorie_trend([], [], now=NOW, lookback=3)

    assert result == [
        {"period_start": "2026-07-06", "estimate_kcal": 0},
        {"period_start": "2026-07-13", "estimate_kcal": 0},
        {"period_start": "2026-07-20", "estimate_kcal": 0},
    ]
