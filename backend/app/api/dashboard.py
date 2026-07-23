"""Dashboard route (FR-3, ADR-0006/ADR-0002): `GET /api/v1/dashboard` fetches
`orders`/`order_items` fresh on every request (AC-7 — no caching, no
precomputed snapshot) and assembles the response contract from
docs/architecture/features/4-spend-dashboard.md §3 via `analytics/aggregate.py`.

Captures one `now` here and threads it into every aggregate call so
`totals`/`trend`/`projection` never disagree about "today" within a response
(§3 notes).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from app.analytics import aggregate
from app.core.auth import AuthedUser, get_current_user
from app.core.db import user_client

router = APIRouter()


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@router.get("/dashboard")
async def get_dashboard(user: AuthedUser = Depends(get_current_user)) -> dict[str, Any]:
    now = datetime.now(UTC)
    client = user_client(user.jwt)
    try:
        all_order_ids = client.select("orders", columns="id")
        orders = client.select("orders", filters={"is_cancelled": "eq.false"})
        order_ids = [order["id"] for order in orders]
        order_items = (
            client.select("order_items", filters={"order_id": f"in.({','.join(order_ids)})"})
            if order_ids
            else []
        )
    finally:
        client.close()

    return {
        "generated_at": _iso_z(now),
        "has_data": len(all_order_ids) > 0,
        "totals": aggregate.spend_totals(orders, now=now),
        "trend": {
            "weekly": aggregate.spend_trend(orders, bucket="week", now=now),
            "monthly": aggregate.spend_trend(orders, bucket="month", now=now),
        },
        "category_breakdown": aggregate.category_breakdown(orders, order_items),
        "top_restaurants": aggregate.top_restaurants(orders),
        "top_products": aggregate.top_products(orders, order_items),
        "order_stats": aggregate.order_stats(orders),
        "projection": aggregate.spend_projection(orders, now=now),
        "location_lens": aggregate.location_lens(orders),
    }
