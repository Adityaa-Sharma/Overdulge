"""Swiggy Instamart adapter — fetch_orders (issue #50, ADR-0005 §3, BRD §2.5-§2.7).

Calls `get_orders` only. `orderType` is always the literal `"DASH"` below —
`"INSTAMART"` is never a valid value (BRD §2.7, silently returns empty) and
never appears anywhere in this file; this is a structural guarantee (one
constant, one call site), not just a convention, per ADR-0005 §2.

Instamart returns plain rupee numbers (BRD §2.5) and an already-UTC
ISO-8601 timestamp in `createdAt` (BRD §2.6, no detail call needed).
`grand_total_paise` is converted from the order's `totalAmount` verbatim, and
the item/fee breakdown from `billDetails` — no arithmetic on any `*_paise`
field anywhere in this file (BRD §2.8).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.sync.normalize import NormalizedOrder, NormalizedOrderItem

# Matches `mcp/client.py::call_tool`'s signature exactly, so orchestration
# can pass that function directly and tests can pass a fake with the same
# shape without touching HTTP transport at all.
McpCaller = Callable[[str, str, str, dict[str, Any]], dict[str, Any]]

_TOOL_NAME = "get_orders"
_ORDER_TYPE = "DASH"
_CANCELLED_STATUSES = {"CANCELLED"}


def fetch_orders(client: McpCaller, base_url: str, access_token: str) -> list[NormalizedOrder]:
    """Fetches and normalizes every Instamart order visible to this account."""
    result = client(base_url, access_token, _TOOL_NAME, {"orderType": _ORDER_TYPE})
    return [_normalize_order(order) for order in result.get("orders", [])]


def _rupees_to_paise(amount: float | int) -> int:
    return round(amount * 100)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _normalize_order(order: dict[str, Any]) -> NormalizedOrder:
    status = order["status"]
    # The money breakdown lives under `billDetails`; the order timestamp is
    # `createdAt` (UTC, with a Z). Confirmed against the live API.
    bill = order.get("billDetails") or {}
    item_total = bill.get("itemTotal")
    fees = bill.get("deliveryFee")
    return NormalizedOrder(
        platform_order_id=str(order["orderId"]),
        status=status,
        is_cancelled=status.upper() in _CANCELLED_STATUSES,
        ordered_at=_parse_timestamp(order["createdAt"]),
        grand_total_paise=_rupees_to_paise(order["totalAmount"]),
        raw=order,
        vendor_name=order.get("storeName"),
        address_id=None,
        item_total_paise=_rupees_to_paise(item_total) if item_total is not None else None,
        fees_paise=_rupees_to_paise(fees) if fees is not None else None,
        items=[_normalize_item(item) for item in order.get("items", [])],
    )


def _normalize_item(item: dict[str, Any]) -> NormalizedOrderItem:
    price = item.get("price")
    return NormalizedOrderItem(
        name=item["name"],
        quantity=item["quantity"],
        unit_price_paise=_rupees_to_paise(price) if price is not None else None,
        platform_item_id=item.get("itemId"),
        product_variant_id=item.get("productVariantId"),
        category=item.get("category"),
        is_veg=item.get("isVeg"),
    )
