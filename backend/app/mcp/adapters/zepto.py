"""Zepto order-history adapter (BRD §2.5/§2.6, ADR-0005 §3, issue #51), plus
the FR-7 recommendation wrappers (issue #39, ADR-0004).

Calls only `list_order_history`, `get_order_detail`, `get_past_order_items`,
`search_products` — never a mutating tool (NFR-1). Zepto's list view carries
no order date, so every order's timestamp is resolved with a
`get_order_detail` call per order, on every sync (same per-sync-call
tradeoff as the Food adapter, ADR-0005 §3 — no cache in Phase 1). Zepto
already reports money as integer paise, so `grand_total_paise` is a verbatim
passthrough of the detail response's `grandTotal` — no arithmetic on any
`*_paise` value anywhere in this file (BRD §2.8). `address_id` is always
`None`: address-scoping is a Food-only concept (BRD §2.4/§5).

`get_past_order_items`/`search_products` results are never combined with the
order-history calls above; `redirect_url` for every recommendation item is
built here from Zepto's own product-page URL shape
(`/pn/<slug>/pvid/<productVariantId>`) — platform-specific knowledge that
must not leak into route code (SYSTEM.md §2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.mcp.client import call_tool
from app.mcp.recommendations import SearchResultItem, slugify
from app.sync.normalize import NormalizedOrder, NormalizedOrderItem

_PRODUCT_URL = "https://www.zeptonow.com/pn/{slug}/pvid/{product_variant_id}"


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


@dataclass
class ZeptoUsualItem:
    """One row of Zepto's own pre-aggregated frequency ranking (BRD §2.9).

    `product_variant_id` is the stable join key back to product data
    (AC-2) — Zepto's own identifier, never re-derived from `name`.
    """

    product_variant_id: str
    name: str
    frequency_rank: int
    unit_price_paise: int | None
    redirect_url: str


def get_usual_items(
    client: httpx.BaseTransport | None, base_url: str, access_token: str
) -> list[ZeptoUsualItem]:
    """Wraps `get_past_order_items` — Zepto's pre-aggregated usuals ranking
    (FR-7.1, AC-1/AC-2)."""
    result = call_tool(base_url, access_token, "get_past_order_items", {}, transport=client)
    return [_normalize_usual_item(item) for item in result.get("items", [])]


def _normalize_usual_item(item: dict[str, Any]) -> ZeptoUsualItem:
    product_variant_id = item["productVariantId"]
    name = item["name"]
    return ZeptoUsualItem(
        product_variant_id=product_variant_id,
        name=name,
        frequency_rank=item["frequencyRank"],
        unit_price_paise=item.get("price"),
        redirect_url=_product_redirect_url(name, product_variant_id),
    )


def search_products(
    client: httpx.BaseTransport | None, base_url: str, access_token: str, query: str
) -> list[SearchResultItem]:
    """Wraps `search_products` — a live, per-request catalogue search
    (FR-7.2, ADR-0004 §2). Never cached: called synchronously on every
    request that needs it, not from a stored/periodic sync.
    """
    result = call_tool(
        base_url, access_token, "search_products", {"query": query}, transport=client
    )
    return [_normalize_search_result(item) for item in result.get("results", [])]


def _normalize_search_result(item: dict[str, Any]) -> SearchResultItem:
    name = item["name"]
    return SearchResultItem(
        name=name,
        unit_price_paise=item.get("price"),
        redirect_url=_product_redirect_url(name, item["productVariantId"]),
        raw=item,
    )


def _product_redirect_url(name: str, product_variant_id: str) -> str:
    """Zepto's own product-page URL shape: `/pn/<slug>/pvid/<productVariantId>`."""
    return _PRODUCT_URL.format(slug=slugify(name), product_variant_id=product_variant_id)
