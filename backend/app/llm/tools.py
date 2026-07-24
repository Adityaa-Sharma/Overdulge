"""Read-only query tool layer for the NL query engine (FR-4; ADR-0003:
docs/architecture/decisions/0003-nl-query-engine-tool-calling.md).

Every function is a thin wrapper over `core/db.py`'s `user_client(jwt)`
(user-JWT-forwarding mode only — RLS is the isolation mechanism per
ADR-0002, so no function here takes or forwards a `user_id` argument).
Every function excludes cancelled orders by default (BRD §2.9/FR-2.3).
`spend_total`/`spend_by_platform` state order-level figures, so they sum
`orders.grand_total_paise` directly — never recomputed from item components
(BRD §2.8). `spend_by_category`/`top_items` state item-level figures, which
only exist as `order_items` rows, so those two derive their amounts from
`quantity * unit_price_paise` (there is no order-level "category total" to
trust instead).

Out of scope here: the LangChain agent that binds these as tools
(`llm/agent.py`, a separate task) and the API route — nothing in this module
imports LangChain or FastAPI.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from app.core.db import PostgrestClient, user_client

Row = dict[str, Any]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _date_boundary(value: str, *, end_of_day: bool) -> datetime:
    day = date.fromisoformat(value[:10])
    return datetime.combine(day, time.max if end_of_day else time.min, tzinfo=UTC)


def _fetch_orders(client: PostgrestClient, *, platform: str | None = None) -> list[Row]:
    filters = {"is_cancelled": "eq.false"}
    if platform is not None:
        filters["platform"] = f"eq.{platform}"
    return client.select("orders", filters=filters)


def _orders_in_range(orders: list[Row], start_date: str, end_date: str) -> list[Row]:
    range_start = _date_boundary(start_date, end_of_day=False)
    range_end = _date_boundary(end_date, end_of_day=True)
    return [
        order
        for order in orders
        if range_start <= _parse_timestamp(order["ordered_at"]) <= range_end
    ]


def _fetch_items_for_orders(client: PostgrestClient, orders: list[Row]) -> list[Row]:
    order_ids = [order["id"] for order in orders]
    if not order_ids:
        return []
    return client.select("order_items", filters={"order_id": f"in.({','.join(order_ids)})"})


def spend_total(
    jwt: str,
    start_date: str,
    end_date: str,
    platform: str | None = None,
    category: str | None = None,
    item_name_contains: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, int]:
    """Total spend (and order count) in `[start_date, end_date]`, both dates
    inclusive. `category`/`item_name_contains` narrow to orders containing at
    least one matching `order_items` row; the summed figure stays the order's
    `grand_total_paise`, never a sum of matching item prices.
    """
    client = user_client(jwt, transport=transport)
    try:
        orders = _orders_in_range(_fetch_orders(client, platform=platform), start_date, end_date)
        if category is not None or item_name_contains is not None:
            items = _fetch_items_for_orders(client, orders)
            matching_order_ids = {
                item["order_id"]
                for item in items
                if (category is None or item.get("category") == category)
                and (
                    item_name_contains is None
                    or item_name_contains.lower() in (item.get("name") or "").lower()
                )
            }
            orders = [order for order in orders if order["id"] in matching_order_ids]
    finally:
        client.close()

    return {
        "total_paise": sum(order.get("grand_total_paise") or 0 for order in orders),
        "order_count": len(orders),
    }


def spend_by_category(
    jwt: str,
    start_date: str,
    end_date: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Spend per `order_items.category` in `[start_date, end_date]`, ranked
    highest-spend first. Items with no category are excluded — there is
    nothing meaningful to group them under.
    """
    client = user_client(jwt, transport=transport)
    try:
        orders = _orders_in_range(_fetch_orders(client), start_date, end_date)
        items = _fetch_items_for_orders(client, orders)
    finally:
        client.close()

    totals: dict[str, int] = {}
    for item in items:
        category = item.get("category")
        if category is None:
            continue
        amount = (item.get("quantity") or 0) * (item.get("unit_price_paise") or 0)
        totals[category] = totals.get(category, 0) + amount

    ranked = sorted(totals.items(), key=lambda entry: entry[1], reverse=True)
    return [{"category": category, "total_paise": total_paise} for category, total_paise in ranked]


def spend_by_platform(
    jwt: str,
    start_date: str,
    end_date: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Spend per platform in `[start_date, end_date]`, ranked highest-spend
    first, summed from `orders.grand_total_paise` directly.
    """
    client = user_client(jwt, transport=transport)
    try:
        orders = _orders_in_range(_fetch_orders(client), start_date, end_date)
    finally:
        client.close()

    totals: dict[str, int] = {}
    for order in orders:
        platform = order.get("platform")
        if platform is None:
            continue
        totals[platform] = totals.get(platform, 0) + (order.get("grand_total_paise") or 0)

    ranked = sorted(totals.items(), key=lambda entry: entry[1], reverse=True)
    return [{"platform": platform, "total_paise": total_paise} for platform, total_paise in ranked]


def top_items(
    jwt: str,
    start_date: str,
    end_date: str,
    limit: int = 10,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """The `limit` highest-spend item names in `[start_date, end_date]`,
    ranked by `quantity * unit_price_paise` summed across every matching
    `order_items` row. `order_count` is the number of distinct orders the
    item appeared in, not the number of item rows.
    """
    client = user_client(jwt, transport=transport)
    try:
        orders = _orders_in_range(_fetch_orders(client), start_date, end_date)
        items = _fetch_items_for_orders(client, orders)
    finally:
        client.close()

    totals: dict[str, int] = {}
    orders_by_name: dict[str, set[str]] = {}
    for item in items:
        name = item.get("name")
        if not name:
            continue
        amount = (item.get("quantity") or 0) * (item.get("unit_price_paise") or 0)
        totals[name] = totals.get(name, 0) + amount
        orders_by_name.setdefault(name, set()).add(item["order_id"])

    ranked = sorted(totals.items(), key=lambda entry: entry[1], reverse=True)[:limit]
    return [
        {"name": name, "order_count": len(orders_by_name[name]), "total_paise": total_paise}
        for name, total_paise in ranked
    ]


def data_coverage(
    jwt: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """The synced order-history window: earliest/latest `ordered_at` and the
    distinct set of platforms with at least one non-cancelled order. Callers
    use this to tell "no data for this range" apart from a genuine ₹0
    (ADR-0003).
    """
    client = user_client(jwt, transport=transport)
    try:
        orders = _fetch_orders(client)
    finally:
        client.close()

    if not orders:
        return {"earliest_order_at": None, "latest_order_at": None, "platforms_synced": []}

    timestamps = [_parse_timestamp(order["ordered_at"]) for order in orders]
    platforms_synced = sorted({order["platform"] for order in orders if order.get("platform")})
    return {
        "earliest_order_at": min(timestamps).isoformat().replace("+00:00", "Z"),
        "latest_order_at": max(timestamps).isoformat().replace("+00:00", "Z"),
        "platforms_synced": platforms_synced,
    }
