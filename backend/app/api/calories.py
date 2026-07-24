"""Calorie rollup + weekly commentary routes (FR-6, #82;
docs/architecture/features/7-calorie-estimation.md §6, ADR-0008 §5). Both
user-JWT-forwarding (ADR-0002), matching the dashboard's read pattern — no
service-role. `/calories/commentary` is its own lazy route, called by the
frontend only after the rollup above renders, so a slow or failing LLM
round-trip never blocks the (fast, DB-only) totals/trend — same latency
isolation as `/budgets/suggestions` (ADR-0007 §2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from app.analytics import aggregate
from app.core import orders as orders_core
from app.core.auth import AuthedUser, get_current_user
from app.core.db import user_client
from app.llm import calories

router = APIRouter()


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _week_start(now: datetime) -> datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=now.weekday())


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@router.get("/calories")
async def get_calories(user: AuthedUser = Depends(get_current_user)) -> dict[str, Any]:
    now = datetime.now(UTC)
    client = user_client(user.jwt)
    try:
        all_orders = orders_core.list_orders(client, user_id=user.user_id)
        orders = orders_core.list_orders(client, user_id=user.user_id, is_cancelled=False)
        order_items = orders_core.list_order_items_for_orders(
            client, order_ids=[order["id"] for order in orders]
        )
    finally:
        client.close()

    return {
        "generated_at": _iso_z(now),
        "has_data": len(all_orders) > 0,
        "totals": aggregate.calorie_totals(orders, order_items, now=now),
        "trend": {"weekly": aggregate.calorie_trend(orders, order_items, now=now)},
    }


@router.get("/calories/commentary")
async def get_calories_commentary(user: AuthedUser = Depends(get_current_user)) -> dict[str, str]:
    now = datetime.now(UTC)
    client = user_client(user.jwt)
    try:
        orders = orders_core.list_orders(client, user_id=user.user_id, is_cancelled=False)
        order_items = orders_core.list_order_items_for_orders(
            client, order_ids=[order["id"] for order in orders]
        )
    finally:
        client.close()

    week_start = _week_start(now)
    week_order_count = sum(
        1 for order in orders if _parse_timestamp(order["ordered_at"]) >= week_start
    )
    week_spend_paise = aggregate.spend_totals(orders, now=now)["this_week_paise"]["combined"]
    week_kcal_estimate = aggregate.calorie_totals(orders, order_items, now=now)[
        "this_week_estimate_kcal"
    ]

    blurb = calories.generate_weekly_blurb(
        week_kcal_estimate=week_kcal_estimate,
        week_order_count=week_order_count,
        week_spend_paise=week_spend_paise,
    )
    return {"blurb": blurb}
