"""Swiggy Food adapter (BRD §2.4/§2.6, ADR-0005 §3): resolves an
address-scoped order history into `NormalizedOrder`s.

`get_addresses` is called first; `get_food_orders` is then called per
returned `addressId`, and the results are merged into one order set keyed
by order id (an order cannot appear under two addresses — BRD §2.4).
`address_id` is carried onto each `NormalizedOrder` so FR-3's location lens
has it.

The list view's timestamp has no year (`"February 12, 0:26 AM"`); resolving
it requires a `get_food_order_details` call per order, on every sync (Phase
1 has no cache for this — ADR-0005 §3, accepted N+1 cost at <25-user scale).

Only calls `get_addresses`, `get_food_orders`, `get_food_order_details`,
`search_menu` — never a mutating tool name anywhere in this file (NFR-1).

`search_menu` (FR-7.2, issue #39, ADR-0004) is a separate, unrelated call
path from order-history sync above: a live, per-request menu search whose
results are never cached or persisted (ADR-0004 §2). `redirect_url` is built
here from Swiggy's own restaurant-page URL shape — platform-specific
knowledge that must not leak into route code (SYSTEM.md §2).

`usual_redirect_url` (FR-7.1, issue #40) backs the computed Food usuals
ranking's redirect target — see its own docstring for why it cannot use the
same restaurant-page shape as `search_menu`'s results.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from app.mcp.recommendations import SearchResultItem, slugify
from app.sync.normalize import NormalizedOrder

# Matches `mcp/client.py::call_tool`'s signature exactly, so orchestration
# can pass that function directly and tests can pass a fake with the same
# shape without touching HTTP transport at all.
McpCaller = Callable[[str, str, str, dict[str, Any]], dict[str, Any]]

_CANCELLED_STATUSES = {"CANCELLED"}
_RESTAURANT_URL = "https://www.swiggy.com/restaurants/{slug}-rest{restaurant_id}"
_SEARCH_URL = "https://www.swiggy.com/search?query={query}"

# Swiggy Food's detail timestamps are wall-clock IST with no offset
# (e.g. "2022-03-21 22:32:11"); the platform is India-only. Instamart, by
# contrast, already sends UTC with a Z — so only Food needs this.
_IST = timezone(timedelta(hours=5, minutes=30))


def fetch_orders(client: McpCaller, base_url: str, access_token: str) -> list[NormalizedOrder]:
    addresses = client(base_url, access_token, "get_addresses", {})["addresses"]
    # get_addresses identifies each address as `id`; get_food_orders takes it
    # back as the `addressId` argument (the two tools name the same value
    # differently — confirmed against the live API).
    address_ids = [address["id"] for address in addresses]
    label_by_address_id = {address["id"]: _address_label(address) for address in addresses}

    raw_orders_by_id: dict[str, dict[str, Any]] = {}
    address_id_by_order_id: dict[str, str] = {}
    for address_id in address_ids:
        result = client(base_url, access_token, "get_food_orders", {"addressId": address_id})
        for raw_order in result["orders"]:
            order_id = str(raw_order["orderId"])
            if order_id in raw_orders_by_id:
                continue
            raw_orders_by_id[order_id] = raw_order
            address_id_by_order_id[order_id] = address_id

    return [
        _normalize_order(
            client,
            base_url,
            access_token,
            order_id,
            raw_order,
            address_id_by_order_id[order_id],
            label_by_address_id.get(address_id_by_order_id[order_id]),
        )
        for order_id, raw_order in raw_orders_by_id.items()
    ]


def _address_label(address: dict[str, Any]) -> str | None:
    """A human name for the address, for the spend-by-location lens.

    Prefer the user's own tag ("Home", "Work", "Garima"); fall back to the
    category, then the first, most-specific segment of the full address line.
    """
    tag = (address.get("addressTag") or "").strip()
    if tag:
        return tag
    category = (address.get("addressCategory") or "").strip()
    if category:
        return category
    line = (address.get("addressLine") or "").strip()
    if line:
        # "Garima: E-135, ..." / "Aditya Sharma: Iiit Lucknow, ..." — the head
        # before the first comma is the most recognisable part.
        return line.split(",")[0].strip() or None
    return None


def _normalize_order(
    client: McpCaller,
    base_url: str,
    access_token: str,
    order_id: str,
    raw_order: dict[str, Any],
    address_id: str,
    address_label: str | None,
) -> NormalizedOrder:
    detail = client(base_url, access_token, "get_food_order_details", {"orderId": order_id})
    status = raw_order["orderStatus"]
    return NormalizedOrder(
        platform_order_id=order_id,
        status=status,
        is_cancelled=status.upper() in _CANCELLED_STATUSES,
        ordered_at=_resolve_ordered_at(detail),
        grand_total_paise=_parse_paise(raw_order["orderTotal"]),
        raw=raw_order,
        vendor_name=raw_order.get("restaurantName"),
        address_id=address_id,
        address_label=address_label,
    )


def _parse_paise(formatted_amount: str) -> int:
    """Parses Swiggy Food's formatted rupee string (e.g. `"₹273"`) into
    integer paise (BRD §2.5). Converts the single verbatim total's units
    only — never combined with any other field (BRD §2.8).
    """
    cleaned = formatted_amount.replace("₹", "").replace(",", "").strip()
    return int(Decimal(cleaned) * 100)


def _resolve_ordered_at(detail: dict[str, Any]) -> datetime:
    """`get_food_order_details` nests the order under `order`, and its
    `orderedTime` carries the full year the list view omits. The value is
    naive IST wall-clock, so it is localised before converting to UTC (BRD
    §2.6).
    """
    ordered_time = detail["order"]["orderedTime"]
    parsed = datetime.fromisoformat(ordered_time)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_IST)
    return parsed.astimezone(UTC)


def search_menu(
    client: McpCaller, base_url: str, access_token: str, query: str
) -> list[SearchResultItem]:
    """Wraps `search_menu` — a live, per-request restaurant-menu search
    (FR-7.2, ADR-0004 §2). Never cached: called synchronously on every
    request that needs it, not from a stored/periodic sync.
    """
    result = client(base_url, access_token, "search_menu", {"query": query})
    return [_normalize_search_result(item) for item in result.get("results", [])]


def _normalize_search_result(item: dict[str, Any]) -> SearchResultItem:
    price = item.get("price")
    return SearchResultItem(
        name=item["name"],
        unit_price_paise=_parse_paise(price) if price else None,
        redirect_url=_restaurant_redirect_url(item),
        raw=item,
    )


def _restaurant_redirect_url(item: dict[str, Any]) -> str:
    """Swiggy's own restaurant-page URL shape:
    `/restaurants/<slug>-rest<restaurantId>`.
    """
    restaurant_name = item.get("restaurantName") or item["name"]
    return _RESTAURANT_URL.format(slug=slugify(restaurant_name), restaurant_id=item["restaurantId"])


def usual_redirect_url(item_name: str) -> str:
    """Redirect target for a computed Food usuals item (FR-7.1, issue #40).

    Order-history sync (`get_food_orders`/`get_food_order_details`) never
    returns a `restaurantId` — only `search_menu` results do (see
    `_restaurant_redirect_url` above) — so a usual grouped from synced
    `order_items` has no id to build a direct restaurant-page link from, and
    a usual's order history can also span more than one restaurant. Falls
    back to Swiggy's own search-results page for the item name: still a
    redirect to the platform's own site (FR-7.3), just not a direct deep
    link.
    """
    return _SEARCH_URL.format(query=quote(item_name))
