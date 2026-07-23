"""CRUD helpers for `orders` and `order_items` (BRD §5, ADR-0005).

Read-only helpers only — writes to these tables happen exclusively through
`sync/normalize.py::upsert_orders`, the one module ADR-0005 §2 designates as
the sole writer of order data. No business logic here; callers pass in the
client matching their trust level (see ADR-0002).
"""

from __future__ import annotations

from typing import Any

from app.core.db import PostgrestClient


def list_orders(
    client: PostgrestClient,
    *,
    user_id: str,
    platform: str | None = None,
    is_cancelled: bool | None = None,
) -> list[dict[str, Any]]:
    filters = {"user_id": f"eq.{user_id}"}
    if platform is not None:
        filters["platform"] = f"eq.{platform}"
    if is_cancelled is not None:
        filters["is_cancelled"] = f"eq.{str(is_cancelled).lower()}"
    return client.select("orders", filters=filters)


def get_order(
    client: PostgrestClient, *, platform: str, platform_order_id: str
) -> dict[str, Any] | None:
    rows = client.select(
        "orders",
        filters={"platform": f"eq.{platform}", "platform_order_id": f"eq.{platform_order_id}"},
    )
    return rows[0] if rows else None


def list_order_items(client: PostgrestClient, *, order_id: str) -> list[dict[str, Any]]:
    return client.select("order_items", filters={"order_id": f"eq.{order_id}"})


def list_order_items_for_orders(
    client: PostgrestClient, *, order_ids: list[str]
) -> list[dict[str, Any]]:
    """Every `order_items` row belonging to any of `order_ids`, in one call."""
    if not order_ids:
        return []
    return client.select("order_items", filters={"order_id": f"in.({','.join(order_ids)})"})
