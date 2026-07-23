"""Canonical intermediate order shape and the orders/order_items upsert layer
(BRD §5, ADR-0005 §2).

`NormalizedOrder`/`NormalizedOrderItem` are the shape every platform adapter
(`mcp/adapters/*`, later tasks) produces: paise ints and tz-aware UTC
datetimes already resolved, with all platform-specific parsing already done
by the adapter. `grand_total_paise` is copied verbatim from the platform
payload everywhere in this module — BRD §2.8 forbids ever recomputing a
total from its components, so no arithmetic (`+`, `sum(`) touches any
`*_paise` field anywhere below.

This module is platform-agnostic: `upsert_orders` takes `platform` as a
plain parameter and stamps it onto every row; it never branches on its
value. It is the only module that talks to `db.py` for order data — reads
go through `core/orders.py` instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.db import PostgrestClient


@dataclass
class NormalizedOrderItem:
    name: str
    quantity: int
    unit_price_paise: int | None = None
    platform_item_id: str | None = None
    product_variant_id: str | None = None
    category: str | None = None
    is_veg: bool | None = None
    calorie_estimate: int | None = None


@dataclass
class NormalizedOrder:
    platform_order_id: str
    status: str
    is_cancelled: bool
    ordered_at: datetime
    grand_total_paise: int
    raw: dict[str, Any]
    vendor_name: str | None = None
    address_id: str | None = None
    item_total_paise: int | None = None
    fees_paise: int | None = None
    items: list[NormalizedOrderItem] = field(default_factory=list)


@dataclass
class UpsertResult:
    orders_upserted: int
    order_items_upserted: int


def _order_row(user_id: str, platform: str, order: NormalizedOrder) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "platform": platform,
        "vendor_name": order.vendor_name,
        "platform_order_id": order.platform_order_id,
        "address_id": order.address_id,
        "status": order.status,
        "is_cancelled": order.is_cancelled,
        "ordered_at": order.ordered_at.isoformat(),
        "grand_total_paise": order.grand_total_paise,
        "item_total_paise": order.item_total_paise,
        "fees_paise": order.fees_paise,
        "raw": order.raw,
    }


def _order_item_row(order_id: str, item: NormalizedOrderItem) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "name": item.name,
        "quantity": item.quantity,
        "unit_price_paise": item.unit_price_paise,
        "platform_item_id": item.platform_item_id,
        "product_variant_id": item.product_variant_id,
        "category": item.category,
        "is_veg": item.is_veg,
        "calorie_estimate": item.calorie_estimate,
    }


def upsert_orders(
    client: PostgrestClient,
    *,
    user_id: str,
    platform: str,
    orders: list[NormalizedOrder],
) -> UpsertResult:
    if not orders:
        return UpsertResult(orders_upserted=0, order_items_upserted=0)

    order_rows = [_order_row(user_id, platform, order) for order in orders]
    upserted = client.upsert("orders", order_rows, on_conflict="platform,platform_order_id")
    order_id_by_platform_order_id = {row["platform_order_id"]: row["id"] for row in upserted}

    order_items_upserted = 0
    for order in orders:
        order_id = order_id_by_platform_order_id[order.platform_order_id]
        client.delete("order_items", filters={"order_id": f"eq.{order_id}"})
        if not order.items:
            continue
        item_rows = [_order_item_row(order_id, item) for item in order.items]
        client.insert("order_items", item_rows)
        order_items_upserted += len(item_rows)

    return UpsertResult(orders_upserted=len(upserted), order_items_upserted=order_items_upserted)
