"""Zepto order-history adapter (BRD §2.5/§2.6, ADR-0005 §3, issue #51).

Calls only `list_order_history` and `get_order_detail` — never a mutating
tool (NFR-1). Zepto's list view carries no order date, so every order's
timestamp is resolved with a `get_order_detail` call per order, on every
sync (same per-sync-call tradeoff as the Food adapter, ADR-0005 §3 — no
cache in Phase 1). Zepto already reports money as integer paise, so
`grand_total_paise` is a verbatim passthrough of the detail response's
`grandTotal` — no arithmetic on any `*_paise` value anywhere in this file
(BRD §2.8). `address_id` is always `None`: address-scoping is a Food-only
concept (BRD §2.4/§5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.mcp.client import call_tool
from app.sync.normalize import NormalizedOrder, NormalizedOrderItem


def fetch_orders(
    client: httpx.BaseTransport | None, base_url: str, access_token: str
) -> list[NormalizedOrder]:
    history = call_tool(base_url, access_token, "list_order_history", {}, transport=client)
    summaries = history.get("orders", [])

    orders: list[NormalizedOrder] = []
    for summary in summaries:
        detail = call_tool(
            base_url,
            access_token,
            "get_order_detail",
            {"orderId": summary["orderId"]},
            transport=client,
        )
        orders.append(_normalize_order(detail))
    return orders


def _normalize_order(detail: dict[str, Any]) -> NormalizedOrder:
    status = detail["status"]
    return NormalizedOrder(
        platform_order_id=detail["orderId"],
        status=status,
        is_cancelled=status.upper() == "CANCELLED",
        ordered_at=datetime.fromisoformat(detail["orderedAt"]),
        grand_total_paise=detail["grandTotal"],
        raw=detail,
        vendor_name=None,
        address_id=None,
        item_total_paise=detail.get("itemTotal"),
        fees_paise=detail.get("fees"),
        items=[_normalize_item(item) for item in detail.get("items", [])],
    )


def _normalize_item(item: dict[str, Any]) -> NormalizedOrderItem:
    return NormalizedOrderItem(
        name=item["name"],
        quantity=item["quantity"],
        unit_price_paise=item.get("price"),
        platform_item_id=item.get("itemId"),
        product_variant_id=item.get("productVariantId"),
        category=item.get("category"),
        is_veg=item.get("isVeg"),
    )
