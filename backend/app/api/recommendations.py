"""Recommendations routes (FR-7, ADR-0004): `GET /api/v1/recommendations/usuals`
combines Zepto's `get_past_order_items` and Instamart's `your_go_to_items`
(both pre-aggregated, called live per request) with a computed Swiggy Food
frequency ranking built from already-synced `order_items` — no equivalent
Food tool exists (ADR-0004 §1) — into one ranked list per platform (AC-1).
User-JWT-forwarding mode throughout (ADR-0002): both the `linked_accounts`
lookup and the `orders`/`order_items` aggregate query run against the
caller's own RLS-scoped rows.

`GET /api/v1/recommendations/suggestions` (FR-7.2, issue #41) reuses the same
per-platform frequent-item lists and, for each one, fans out to a live
`search_products`/`search_menu` call to find a qualifying alternative
(ADR-0004 §4): cheaper (`unit_price_paise` below the frequent item's own
average), or lower-calorie when both the frequent item and the candidate have
a calorie estimate. Calorie estimates for live candidate names come from
`llm/calories.py::estimate_calories`, gated by `is_calorie_eligible` exactly
like ingest-time estimation — grocery items (Zepto/Instamart) never get a
synthetic estimate just to enable the comparison (AC-5's cheaper-only path).

Nothing here is cached or persisted — every list is computed fresh on each
request (ADR-0004 §2).
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends

from app.core import linked_accounts
from app.core.auth import AuthedUser, get_current_user
from app.core.db import PostgrestClient, user_client
from app.core.orders import list_order_items_for_orders, list_orders
from app.llm import calories as llm_calories
from app.mcp.adapters import swiggy_food, swiggy_instamart, zepto
from app.mcp.client import call_tool
from app.mcp.recommendations import SearchResultItem
from app.oauth import engine
from app.oauth.platforms import swiggy as swiggy_platform
from app.oauth.platforms import zepto as zepto_platform

router = APIRouter()

# Each MCP surface lives at its own path under the platform host (see
# `sync/cron.py`'s identical convention) — Instamart at `/im`, Zepto at `/mcp`,
# Swiggy Food at `/food`. Food usuals (FR-7.1) need no MCP call at all
# (computed from already-synced order_items); Food suggestions (FR-7.2) do,
# via `search_menu`.
_INSTAMART_MCP_PATH = "/im"
_ZEPTO_MCP_PATH = "/mcp"
_SWIGGY_FOOD_MCP_PATH = "/food"


def _mcp_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


@router.get("/recommendations/usuals")
async def get_usuals(
    user: AuthedUser = Depends(get_current_user),
) -> dict[str, list[dict[str, Any]]]:
    client = user_client(user.jwt)
    try:
        zepto_account = linked_accounts.get_linked_account(
            client, user_id=user.user_id, platform="zepto"
        )
        swiggy_account = linked_accounts.get_linked_account(
            client, user_id=user.user_id, platform="swiggy"
        )

        zepto_items = (
            _zepto_usuals(client, user_id=user.user_id, account=zepto_account)
            if zepto_account is not None
            else []
        )
        instamart_items = (
            _instamart_usuals(client, user_id=user.user_id, account=swiggy_account)
            if swiggy_account is not None
            else []
        )
        food_items = _food_usuals(client, user_id=user.user_id)
    finally:
        client.close()

    return {
        "zepto": zepto_items,
        "swiggy_instamart": instamart_items,
        "swiggy_food": food_items,
    }


@router.get("/recommendations/suggestions")
async def get_suggestions(
    user: AuthedUser = Depends(get_current_user),
) -> dict[str, list[dict[str, Any]]]:
    client = user_client(user.jwt)
    try:
        zepto_account = linked_accounts.get_linked_account(
            client, user_id=user.user_id, platform="zepto"
        )
        swiggy_account = linked_accounts.get_linked_account(
            client, user_id=user.user_id, platform="swiggy"
        )

        zepto_suggestions = (
            _zepto_suggestions(client, user_id=user.user_id, account=zepto_account)
            if zepto_account is not None
            else []
        )
        instamart_suggestions = (
            _instamart_suggestions(client, user_id=user.user_id, account=swiggy_account)
            if swiggy_account is not None
            else []
        )
        # Food *usuals* need no account (computed from already-synced
        # order_items), but a Food *suggestion* does — `search_menu` is a
        # live MCP call and needs an access token, unlike the usuals path.
        food_suggestions = (
            _food_suggestions(client, user_id=user.user_id, account=swiggy_account)
            if swiggy_account is not None
            else []
        )
    finally:
        client.close()

    return {
        "zepto": zepto_suggestions,
        "swiggy_instamart": instamart_suggestions,
        "swiggy_food": food_suggestions,
    }


def _zepto_usuals(
    client: PostgrestClient, *, user_id: str, account: dict[str, Any]
) -> list[dict[str, Any]]:
    access_token = engine.resolve_access_token(
        client,
        zepto_platform.CONFIG,
        user_id=user_id,
        platform="zepto",
        tokens_encrypted=account["tokens_encrypted"],
    )
    mcp_url = _mcp_url(zepto_platform.CONFIG.mcp_base_url, _ZEPTO_MCP_PATH)
    items = zepto.get_usual_items(None, mcp_url, access_token)
    return [
        {
            "platform": "zepto",
            "key": item.product_variant_id,
            "name": item.name,
            "frequency_rank_or_count": item.frequency_rank,
            "avg_unit_price_paise": item.unit_price_paise,
            "calorie_estimate": None,
            "redirect_url": item.redirect_url,
        }
        for item in sorted(items, key=lambda usual: usual.frequency_rank)
    ]


def _instamart_usuals(
    client: PostgrestClient, *, user_id: str, account: dict[str, Any]
) -> list[dict[str, Any]]:
    access_token = engine.resolve_access_token(
        client,
        swiggy_platform.CONFIG,
        user_id=user_id,
        platform="swiggy",
        tokens_encrypted=account["tokens_encrypted"],
    )
    mcp_url = _mcp_url(swiggy_platform.CONFIG.mcp_base_url, _INSTAMART_MCP_PATH)
    items = swiggy_instamart.get_usual_items(call_tool, mcp_url, access_token)
    return [
        {
            "platform": "swiggy_instamart",
            "key": item.item_id,
            "name": item.name,
            "frequency_rank_or_count": item.frequency_rank,
            "avg_unit_price_paise": item.unit_price_paise,
            "calorie_estimate": None,
            "redirect_url": item.redirect_url,
        }
        for item in sorted(items, key=lambda usual: usual.frequency_rank)
    ]


def _food_usuals(client: PostgrestClient, *, user_id: str) -> list[dict[str, Any]]:
    orders = list_orders(client, user_id=user_id, platform="swiggy_food", is_cancelled=False)
    order_ids = [order["id"] for order in orders]
    order_items = list_order_items_for_orders(client, order_ids=order_ids)

    names_by_key: dict[str, str] = {}
    prices_by_key: dict[str, list[int]] = defaultdict(list)
    calorie_estimate_by_key: dict[str, int | None] = {}
    order_ids_by_key: dict[str, set[str]] = defaultdict(set)

    for item in order_items:
        raw_name = (item.get("name") or "").strip()
        key = raw_name.lower()
        if not key:
            continue
        names_by_key.setdefault(key, raw_name)
        order_ids_by_key[key].add(item["order_id"])
        price = item.get("unit_price_paise")
        if price is not None:
            prices_by_key[key].append(price)
        if calorie_estimate_by_key.get(key) is None and item.get("calorie_estimate") is not None:
            calorie_estimate_by_key[key] = item["calorie_estimate"]

    ranked_keys = sorted(names_by_key, key=lambda key: (-len(order_ids_by_key[key]), key))
    return [
        {
            "platform": "swiggy_food",
            "key": key,
            "name": names_by_key[key],
            "frequency_rank_or_count": len(order_ids_by_key[key]),
            "avg_unit_price_paise": (
                round(sum(prices_by_key[key]) / len(prices_by_key[key]))
                if prices_by_key[key]
                else None
            ),
            "calorie_estimate": calorie_estimate_by_key.get(key),
            "redirect_url": swiggy_food.usual_redirect_url(names_by_key[key]),
        }
        for key in ranked_keys
    ]


def _zepto_suggestions(
    client: PostgrestClient, *, user_id: str, account: dict[str, Any]
) -> list[dict[str, Any]]:
    frequent_items = _zepto_usuals(client, user_id=user_id, account=account)
    if not frequent_items:
        return []
    access_token = engine.resolve_access_token(
        client,
        zepto_platform.CONFIG,
        user_id=user_id,
        platform="zepto",
        tokens_encrypted=account["tokens_encrypted"],
    )
    mcp_url = _mcp_url(zepto_platform.CONFIG.mcp_base_url, _ZEPTO_MCP_PATH)
    suggestions = []
    for frequent_item in frequent_items:
        candidates = zepto.search_products(None, mcp_url, access_token, frequent_item["name"])
        suggestion = _pick_suggestion("zepto", frequent_item, candidates)
        if suggestion is not None:
            suggestions.append(suggestion)
    return suggestions


def _instamart_suggestions(
    client: PostgrestClient, *, user_id: str, account: dict[str, Any]
) -> list[dict[str, Any]]:
    frequent_items = _instamart_usuals(client, user_id=user_id, account=account)
    if not frequent_items:
        return []
    access_token = engine.resolve_access_token(
        client,
        swiggy_platform.CONFIG,
        user_id=user_id,
        platform="swiggy",
        tokens_encrypted=account["tokens_encrypted"],
    )
    mcp_url = _mcp_url(swiggy_platform.CONFIG.mcp_base_url, _INSTAMART_MCP_PATH)
    suggestions = []
    for frequent_item in frequent_items:
        candidates = swiggy_instamart.search_products(
            call_tool, mcp_url, access_token, frequent_item["name"]
        )
        suggestion = _pick_suggestion("swiggy_instamart", frequent_item, candidates)
        if suggestion is not None:
            suggestions.append(suggestion)
    return suggestions


def _food_suggestions(
    client: PostgrestClient, *, user_id: str, account: dict[str, Any]
) -> list[dict[str, Any]]:
    frequent_items = _food_usuals(client, user_id=user_id)
    if not frequent_items:
        return []
    access_token = engine.resolve_access_token(
        client,
        swiggy_platform.CONFIG,
        user_id=user_id,
        platform="swiggy",
        tokens_encrypted=account["tokens_encrypted"],
    )
    mcp_url = _mcp_url(swiggy_platform.CONFIG.mcp_base_url, _SWIGGY_FOOD_MCP_PATH)
    suggestions = []
    for frequent_item in frequent_items:
        candidates = swiggy_food.search_menu(
            call_tool, mcp_url, access_token, frequent_item["name"]
        )
        suggestion = _pick_suggestion("swiggy_food", frequent_item, candidates)
        if suggestion is not None:
            suggestions.append(suggestion)
    return suggestions


def _pick_suggestion(
    platform: str, frequent_item: dict[str, Any], candidates: list[SearchResultItem]
) -> dict[str, Any] | None:
    """Picks the best qualifying live candidate for one frequent item
    (ADR-0004 §4), or `None` when nothing qualifies (AC-5 — the caller omits
    the item entirely, never a placeholder).

    A candidate qualifies if it is cheaper than the frequent item's own
    average price, or lower-calorie when both have a calorie estimate.
    `estimate_calories` is only ever called for a candidate when the
    frequent item's own platform/category is calorie-eligible *and* it
    already carries an estimate — grocery items (Zepto/Instamart, whose live
    search results carry no `category`) are never assigned a synthetic
    calorie estimate just to enable a comparison. Among qualifying
    candidates, "best" is lowest price first, lowest calorie estimate as the
    tiebreak (both `None` sort last).
    """
    frequent_price = frequent_item["avg_unit_price_paise"]
    frequent_calorie = frequent_item["calorie_estimate"]
    calorie_eligible = (
        llm_calories.is_calorie_eligible(platform, None) and frequent_calorie is not None
    )

    best: dict[str, Any] | None = None
    best_rank: tuple[float, float] | None = None
    for candidate in candidates:
        candidate_calorie = (
            llm_calories.estimate_calories(candidate.name) if calorie_eligible else None
        )
        cheaper = (
            candidate.unit_price_paise is not None
            and frequent_price is not None
            and candidate.unit_price_paise < frequent_price
        )
        lower_calorie = (
            calorie_eligible
            and candidate_calorie is not None
            and candidate_calorie < frequent_calorie
        )
        if not (cheaper or lower_calorie):
            continue

        rank = (
            candidate.unit_price_paise if candidate.unit_price_paise is not None else math.inf,
            candidate_calorie if candidate_calorie is not None else math.inf,
        )
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = {
                "platform": platform,
                "key": frequent_item["key"],
                "name": frequent_item["name"],
                "avg_unit_price_paise": frequent_price,
                "calorie_estimate": frequent_calorie,
                "suggested_name": candidate.name,
                "suggested_unit_price_paise": candidate.unit_price_paise,
                "suggested_calorie_estimate": candidate_calorie,
                "suggested_redirect_url": candidate.redirect_url,
                "cheaper": cheaper,
                "lower_calorie": lower_calorie,
            }
    return best
