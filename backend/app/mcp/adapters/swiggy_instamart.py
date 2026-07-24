"""Swiggy Instamart adapter — fetch_orders (issue #50, ADR-0005 §3, BRD
§2.5-§2.7), plus the FR-7 recommendation wrappers (issue #39, ADR-0004).

Calls `get_orders`, `your_go_to_items`, `search_products` — never a mutating
tool (NFR-1). `orderType` is always the literal `"DASH"` below —
`"INSTAMART"` is never a valid value (BRD §2.7, silently returns empty) and
never appears anywhere in this file; this is a structural guarantee (one
constant, one call site), not just a convention, per ADR-0005 §2.

Instamart returns plain rupee numbers (BRD §2.5) and an already-UTC
ISO-8601 timestamp in `createdAt` (BRD §2.6, no detail call needed).
`grand_total_paise` is converted from the order's `totalAmount` verbatim, and
the item/fee breakdown from `billDetails` — no arithmetic on any `*_paise`
field anywhere in this file (BRD §2.8).

`your_go_to_items` usual rows key on `item_id` — Instamart's own native
identifier, deliberately not coerced into Zepto's `product_variant_id`
semantics (ADR-0004 §1: mixing join semantics per platform is intentional).
`redirect_url` for every recommendation item is built here from Instamart's
own item-page URL shape — platform-specific knowledge that must not leak
into route code (SYSTEM.md §2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.mcp.recommendations import SearchResultItem
from app.sync.normalize import NormalizedOrder, NormalizedOrderItem

# Matches `mcp/client.py::call_tool`'s signature exactly, so orchestration
# can pass that function directly and tests can pass a fake with the same
# shape without touching HTTP transport at all.
McpCaller = Callable[[str, str, str, dict[str, Any]], dict[str, Any]]

_TOOL_NAME = "get_orders"
_ORDER_TYPE = "DASH"
_CANCELLED_STATUSES = {"CANCELLED"}
_ITEM_URL = "https://www.swiggy.com/instamart/item/{item_id}"


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


@dataclass
class InstamartUsualItem:
    """One row of Instamart's own pre-aggregated `your_go_to_items` ranking
    (FR-7.1). `item_id` is whatever native identifier the tool returns —
    see module docstring on why this is not `product_variant_id`.
    """

    item_id: str
    name: str
    frequency_rank: int
    unit_price_paise: int | None
    redirect_url: str


def get_usual_items(
    client: McpCaller, base_url: str, access_token: str
) -> list[InstamartUsualItem]:
    """Wraps `your_go_to_items` — Instamart's pre-aggregated usuals ranking."""
    result = client(base_url, access_token, "your_go_to_items", {})
    return [_normalize_usual_item(item) for item in result.get("items", [])]


def _normalize_usual_item(item: dict[str, Any]) -> InstamartUsualItem:
    item_id = item["itemId"]
    price = item.get("price")
    return InstamartUsualItem(
        item_id=item_id,
        name=item["name"],
        frequency_rank=item["frequencyRank"],
        unit_price_paise=_rupees_to_paise(price) if price is not None else None,
        redirect_url=_item_redirect_url(item_id),
    )


def search_products(
    client: McpCaller, base_url: str, access_token: str, query: str
) -> list[SearchResultItem]:
    """Wraps `search_products` — a live, per-request catalogue search
    (FR-7.2, ADR-0004 §2). Never cached: called synchronously on every
    request that needs it, not from a stored/periodic sync.
    """
    result = client(base_url, access_token, "search_products", {"query": query})
    return [_normalize_search_result(item) for item in result.get("results", [])]


def _normalize_search_result(item: dict[str, Any]) -> SearchResultItem:
    price = item.get("price")
    return SearchResultItem(
        name=item["name"],
        unit_price_paise=_rupees_to_paise(price) if price is not None else None,
        redirect_url=_item_redirect_url(item["itemId"]),
        raw=item,
    )


def _item_redirect_url(item_id: str) -> str:
    """Instamart's own item-page URL shape: `/instamart/item/<itemId>`."""
    return _ITEM_URL.format(item_id=item_id)
