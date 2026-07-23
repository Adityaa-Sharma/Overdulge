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

Only calls `get_addresses`, `get_food_orders`, `get_food_order_details` —
never a mutating tool name anywhere in this file (NFR-1).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.sync.normalize import NormalizedOrder

# Matches `mcp/client.py::call_tool`'s signature exactly, so orchestration
# can pass that function directly and tests can pass a fake with the same
# shape without touching HTTP transport at all.
McpCaller = Callable[[str, str, str, dict[str, Any]], dict[str, Any]]

_CANCELLED_STATUSES = {"CANCELLED"}


def fetch_orders(client: McpCaller, base_url: str, access_token: str) -> list[NormalizedOrder]:
    addresses = client(base_url, access_token, "get_addresses", {})["addresses"]
    address_ids = [address["addressId"] for address in addresses]

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
            client, base_url, access_token, order_id, raw_order, address_id_by_order_id[order_id]
        )
        for order_id, raw_order in raw_orders_by_id.items()
    ]


def _normalize_order(
    client: McpCaller,
    base_url: str,
    access_token: str,
    order_id: str,
    raw_order: dict[str, Any],
    address_id: str,
) -> NormalizedOrder:
    detail = client(base_url, access_token, "get_food_order_details", {"orderId": order_id})
    status = raw_order["orderStatus"]
    return NormalizedOrder(
        platform_order_id=order_id,
        status=status,
        is_cancelled=status.upper() in _CANCELLED_STATUSES,
        ordered_at=_resolve_ordered_at(detail),
        grand_total_paise=_parse_paise(raw_order["grandTotal"]),
        raw=raw_order,
        vendor_name=raw_order.get("restaurantName"),
        address_id=address_id,
    )


def _parse_paise(formatted_amount: str) -> int:
    """Parses Swiggy Food's formatted rupee string (e.g. `"₹273"`) into
    integer paise (BRD §2.5). Converts the single verbatim total's units
    only — never combined with any other field (BRD §2.8).
    """
    cleaned = formatted_amount.replace("₹", "").replace(",", "").strip()
    return int(Decimal(cleaned) * 100)


def _resolve_ordered_at(detail: dict[str, Any]) -> datetime:
    """The list view's timestamp has no year; `get_food_order_details`
    carries a full, timezone-aware ISO-8601 timestamp, which is the source
    of truth for `ordered_at` (BRD §2.6).
    """
    return datetime.fromisoformat(detail["orderTime"]).astimezone(UTC)
