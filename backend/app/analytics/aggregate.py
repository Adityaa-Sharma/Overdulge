"""Dashboard aggregation over `orders`/`order_items` rows (ADR-0006:
docs/architecture/decisions/0006-dashboard-aggregation-in-python.md;
response shapes: docs/architecture/features/4-spend-dashboard.md §3).

Pure functions only: plain-dict rows in (matching the canonical schema,
BRD §5, plus `orders.vendor_name`), aggregate dicts out. No I/O, no
PostgREST/FastAPI imports, no `datetime.now()` — "now" is always an
explicit parameter so every function in one request agrees on what
"today"/"this week"/"this month" means and tests stay deterministic.

Callers are expected to have already excluded cancelled orders (the route
fetches with `is_cancelled=eq.false`, per ADR-0006 — a read-time concern
owned there, not duplicated here).
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

Row = dict[str, Any]

PLATFORMS: tuple[str, str, str] = ("swiggy_food", "swiggy_instamart", "zepto")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _zeroed_platform_totals() -> dict[str, int]:
    return {"combined": 0, **{platform: 0 for platform in PLATFORMS}}


def _week_start(dt: datetime) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=dt.weekday())


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_months_back(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) - months
    year, month = divmod(total, 12)
    return dt.replace(year=year, month=month + 1)


def _shift_weeks_back(dt: datetime, weeks: int) -> datetime:
    return dt - timedelta(weeks=weeks)


def _grouped_ranking(
    rows: list[Row],
    *,
    group_key: str,
    amount_fn: Callable[[Row], int],
    field_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    groups: dict[Any, dict[str, int]] = {}
    for row in rows:
        key = row.get(group_key)
        if key is None:
            continue
        bucket = groups.setdefault(key, {"spend_paise": 0, "order_count": 0})
        bucket["spend_paise"] += amount_fn(row)
        bucket["order_count"] += 1
    ranked = sorted(groups.items(), key=lambda item: item[1]["spend_paise"], reverse=True)
    return [{field_name: key, **bucket} for key, bucket in ranked[:limit]]


def spend_totals(orders: list[Row], *, now: datetime) -> dict[str, dict[str, int]]:
    week_start = _week_start(now)
    month_start = _month_start(now)
    this_week = _zeroed_platform_totals()
    this_month = _zeroed_platform_totals()

    for order in orders:
        ordered_at = _parse_timestamp(order["ordered_at"])
        amount = order.get("grand_total_paise") or 0
        platform = order.get("platform")

        if ordered_at >= week_start:
            this_week["combined"] += amount
            if platform in this_week:
                this_week[platform] += amount
        if ordered_at >= month_start:
            this_month["combined"] += amount
            if platform in this_month:
                this_month[platform] += amount

    return {"this_week_paise": this_week, "this_month_paise": this_month}


def spend_trend(
    orders: list[Row],
    *,
    bucket: Literal["week", "month"],
    now: datetime,
    lookback: int = 12,
) -> list[dict[str, Any]]:
    if bucket == "week":
        period_start_fn = _week_start
        step = _shift_weeks_back
    elif bucket == "month":
        period_start_fn = _month_start
        step = _shift_months_back
    else:
        raise ValueError(f"unknown bucket: {bucket!r}")

    current_period = period_start_fn(now)
    periods = [step(current_period, n) for n in range(lookback - 1, -1, -1)]
    buckets = {
        period.date().isoformat(): {
            "period_start": period.date().isoformat(),
            "combined_paise": 0,
            "swiggy_food_paise": 0,
            "swiggy_instamart_paise": 0,
            "zepto_paise": 0,
        }
        for period in periods
    }

    for order in orders:
        ordered_at = _parse_timestamp(order["ordered_at"])
        period_row = buckets.get(period_start_fn(ordered_at).date().isoformat())
        if period_row is None:
            continue
        amount = order.get("grand_total_paise") or 0
        period_row["combined_paise"] += amount
        platform = order.get("platform")
        if platform in PLATFORMS:
            period_row[f"{platform}_paise"] += amount

    return [buckets[period.date().isoformat()] for period in periods]


def category_breakdown(orders: list[Row], order_items: list[Row]) -> dict[str, Any]:
    food_delivery_paise = sum(
        order.get("grand_total_paise") or 0
        for order in orders
        if order.get("platform") == "swiggy_food"
    )
    grocery_paise = sum(
        order.get("grand_total_paise") or 0
        for order in orders
        if order.get("platform") in ("swiggy_instamart", "zepto")
    )

    item_categories_paise: dict[str, int] = {}
    for item in order_items:
        category = item.get("category")
        if category is None:
            continue
        amount = (item.get("quantity") or 0) * (item.get("unit_price_paise") or 0)
        item_categories_paise[category] = item_categories_paise.get(category, 0) + amount

    return {
        "food_delivery_paise": food_delivery_paise,
        "grocery_paise": grocery_paise,
        "item_categories_paise": item_categories_paise,
    }


def top_restaurants(orders: list[Row], *, limit: int = 10) -> list[dict[str, Any]]:
    food_orders = [order for order in orders if order.get("platform") == "swiggy_food"]
    return _grouped_ranking(
        food_orders,
        group_key="vendor_name",
        amount_fn=lambda order: order.get("grand_total_paise") or 0,
        field_name="name",
        limit=limit,
    )


def top_products(
    orders: list[Row], order_items: list[Row], *, limit: int = 10
) -> list[dict[str, Any]]:
    grocery_order_ids = {
        order["id"] for order in orders if order.get("platform") in ("swiggy_instamart", "zepto")
    }
    grocery_items = [item for item in order_items if item.get("order_id") in grocery_order_ids]
    return _grouped_ranking(
        grocery_items,
        group_key="name",
        amount_fn=lambda item: (item.get("quantity") or 0) * (item.get("unit_price_paise") or 0),
        field_name="name",
        limit=limit,
    )


def order_stats(orders: list[Row]) -> dict[str, dict[str, int | None]]:
    stats: dict[str, dict[str, int | None]] = {}
    for platform in PLATFORMS:
        platform_orders = [order for order in orders if order.get("platform") == platform]
        order_count = len(platform_orders)
        avg_order_value_paise = (
            round(
                sum(order.get("grand_total_paise") or 0 for order in platform_orders) / order_count
            )
            if order_count
            else None
        )
        stats[platform] = {
            "order_count": order_count,
            "avg_order_value_paise": avg_order_value_paise,
        }
    return stats


def spend_projection(orders: list[Row], *, now: datetime) -> dict[str, Any]:
    month_start = _month_start(now)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_elapsed = (now.date() - month_start.date()).days

    spend_to_date_paise = _zeroed_platform_totals()
    for order in orders:
        ordered_at = _parse_timestamp(order["ordered_at"])
        if ordered_at.year != now.year or ordered_at.month != now.month:
            continue
        amount = order.get("grand_total_paise") or 0
        spend_to_date_paise["combined"] += amount
        platform = order.get("platform")
        if platform in spend_to_date_paise:
            spend_to_date_paise[platform] += amount

    if days_elapsed == 0:
        projected_total_paise = _zeroed_platform_totals()
    else:
        projected_total_paise = {
            key: round(value / days_elapsed * days_in_month)
            for key, value in spend_to_date_paise.items()
        }

    return {
        "month": now.strftime("%Y-%m"),
        "spend_to_date_paise": spend_to_date_paise,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "projected_total_paise": projected_total_paise,
        "label": "Projection",
    }


def _budget_status(pct: float) -> Literal["ok", "near", "over"]:
    if pct >= 1.0:
        return "over"
    if pct >= 0.8:
        return "near"
    return "ok"


def budget_progress(
    orders: list[Row],
    order_items: list[Row],
    budgets: list[Row],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    current_month_order_ids = {
        order["id"]
        for order in orders
        if (parsed := _parse_timestamp(order["ordered_at"])).year == now.year
        and parsed.month == now.month
    }
    current_month_items = [
        item for item in order_items if item.get("order_id") in current_month_order_ids
    ]
    category_spend_paise = category_breakdown([], current_month_items)["item_categories_paise"]
    overall_spent_paise = spend_totals(orders, now=now)["this_month_paise"]["combined"]

    rows = []
    for budget in budgets:
        category = budget.get("category")
        cap_paise = budget["cap_paise"]
        spent_paise = (
            overall_spent_paise if category is None else category_spend_paise.get(category, 0)
        )
        pct = spent_paise / cap_paise
        rows.append(
            {
                "category": category,
                "cap_paise": cap_paise,
                "spent_paise": spent_paise,
                "pct": pct,
                "status": _budget_status(pct),
            }
        )
    return rows


def location_lens(orders: list[Row], *, limit: int = 20) -> list[dict[str, Any]]:
    food_orders = [order for order in orders if order.get("platform") == "swiggy_food"]
    return _grouped_ranking(
        food_orders,
        group_key="address_id",
        amount_fn=lambda order: order.get("grand_total_paise") or 0,
        field_name="address_id",
        limit=limit,
    )
