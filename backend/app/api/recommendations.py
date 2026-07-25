"""Recommendations routes (FR-7, ADR-0004): `GET /api/v1/recommendations/usuals`
combines Zepto's `get_past_order_items` and Instamart's `your_go_to_items`
(both pre-aggregated, called live per request) with a computed Swiggy Food
frequency ranking built from already-synced `order_items` — no equivalent
Food tool exists (ADR-0004 §1) — into one ranked list per platform (AC-1).
User-JWT-forwarding mode throughout (ADR-0002): both the `linked_accounts`
lookup and the `orders`/`order_items` aggregate query run against the
caller's own RLS-scoped rows.

Nothing here is cached or persisted — every list is computed fresh on each
request (ADR-0004 §2). `GET /api/v1/recommendations/suggestions` (FR-7.2,
issue #41) is the only route in this module that calls `search_products`/
`search_menu`: for each frequent item from the usuals computation above, it
fetches live candidates and picks the best one that qualifies on price and/or
calories (ADR-0004 §4). `get_usuals` itself still never fans out to a live
search call per item.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends

from app.core import linked_accounts
from app.core.auth import AuthedUser, get_current_user
from app.core.db import PostgrestClient, user_client
from app.core.orders import list_order_items_for_orders, list_orders
from app.llm.calories import estimate_calories, is_calorie_eligible
from app.mcp.adapters import swiggy_food, swiggy_instamart, zepto
from app.mcp.client import call_tool
from app.mcp.recommendations import SearchResultItem
from app.oauth import engine
from app.oauth.platforms import swiggy as swiggy_platform
from app.oauth.platforms import zepto as zepto_platform

router = APIRouter()

# Each MCP surface lives at its own path under the platform host (see
# `sync/cron.py`'s identical convention) — Instamart at `/im`, Zepto at `/mcp`,
# Swiggy Food at `/food`. Usuals need no Food MCP call (computed from
# already-synced order_items); suggestions does, for `search_menu`.
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

        suggestions: list[dict[str, Any]] = []
        if zepto_account is not None:
            suggestions.extend(
                _zepto_suggestions(client, user_id=user.user_id, account=zepto_account)
            )
        if swiggy_account is not None:
            suggestions.extend(
                _instamart_suggestions(client, user_id=user.user_id, account=swiggy_account)
            )
            suggestions.extend(
                _food_suggestions(client, user_id=user.user_id, account=swiggy_account)
            )
    finally:
        client.close()

    return {"suggestions": suggestions}


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
    access_token = engine.resolve_access_token(
        client,
        zepto_platform.CONFIG,
        user_id=user_id,
        platform="zepto",
        tokens_encrypted=account["tokens_encrypted"],
    )
    mcp_url = _mcp_url(zepto_platform.CONFIG.mcp_base_url, _ZEPTO_MCP_PATH)
    usual_items = zepto.get_usual_items(None, mcp_url, access_token)

    suggestions = []
    for usual in usual_items:
        candidates = zepto.search_products(None, mcp_url, access_token, usual.name)
        alternative = _pick_alternative(
            platform="zepto",
            item_avg_price_paise=usual.unit_price_paise,
            item_calorie_estimate=None,
            candidates=candidates,
        )
        if alternative is not None:
            suggestions.append(
                _suggestion_entry(
                    platform="zepto",
                    key=usual.product_variant_id,
                    name=usual.name,
                    avg_unit_price_paise=usual.unit_price_paise,
                    calorie_estimate=None,
                    alternative=alternative,
                )
            )
    return suggestions


def _instamart_suggestions(
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
    usual_items = swiggy_instamart.get_usual_items(call_tool, mcp_url, access_token)

    suggestions = []
    for usual in usual_items:
        candidates = swiggy_instamart.search_products(call_tool, mcp_url, access_token, usual.name)
        alternative = _pick_alternative(
            platform="swiggy_instamart",
            item_avg_price_paise=usual.unit_price_paise,
            item_calorie_estimate=None,
            candidates=candidates,
        )
        if alternative is not None:
            suggestions.append(
                _suggestion_entry(
                    platform="swiggy_instamart",
                    key=usual.item_id,
                    name=usual.name,
                    avg_unit_price_paise=usual.unit_price_paise,
                    calorie_estimate=None,
                    alternative=alternative,
                )
            )
    return suggestions


def _food_suggestions(
    client: PostgrestClient, *, user_id: str, account: dict[str, Any]
) -> list[dict[str, Any]]:
    items = _food_usuals(client, user_id=user_id)
    if not items:
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
    for item in items:
        candidates = swiggy_food.search_menu(call_tool, mcp_url, access_token, item["name"])
        alternative = _pick_alternative(
            platform="swiggy_food",
            item_avg_price_paise=item["avg_unit_price_paise"],
            item_calorie_estimate=item["calorie_estimate"],
            candidates=candidates,
        )
        if alternative is not None:
            suggestions.append(
                _suggestion_entry(
                    platform="swiggy_food",
                    key=item["key"],
                    name=item["name"],
                    avg_unit_price_paise=item["avg_unit_price_paise"],
                    calorie_estimate=item["calorie_estimate"],
                    alternative=alternative,
                )
            )
    return suggestions


def _pick_alternative(
    *,
    platform: str,
    item_avg_price_paise: int | None,
    item_calorie_estimate: int | None,
    candidates: list[SearchResultItem],
) -> dict[str, Any] | None:
    """Picks the best qualifying live candidate for one frequent item
    (ADR-0004 §4): the cheapest candidate below the frequent item's own
    average price, or — only when no candidate qualifies on price and the
    frequent item itself already has a calorie estimate — the first
    calorie-eligible candidate whose estimate is lower. A candidate's
    eligibility for calorie estimation is decided by the same
    `is_calorie_eligible` FR-6 uses at ingest, never a second ready-to-eat
    rule; a non-ready-to-eat grocery item never gets a synthesized calorie
    estimate just to enable the comparison. Returns `None` when nothing
    qualifies (AC-5) — the caller omits the item entirely, no placeholder.
    """
    cheaper_candidates = [
        candidate
        for candidate in candidates
        if item_avg_price_paise is not None
        and candidate.unit_price_paise is not None
        and candidate.unit_price_paise < item_avg_price_paise
    ]
    if cheaper_candidates:
        best = min(cheaper_candidates, key=lambda candidate: candidate.unit_price_paise)
        return _alternative_payload(best, calorie_estimate=None, cheaper=True, lower_calorie=False)

    if item_calorie_estimate is None:
        return None

    for candidate in candidates:
        if not is_calorie_eligible(platform, candidate.raw.get("category")):
            continue
        candidate_calorie = estimate_calories(candidate.name)
        if candidate_calorie is not None and candidate_calorie < item_calorie_estimate:
            return _alternative_payload(
                candidate, calorie_estimate=candidate_calorie, cheaper=False, lower_calorie=True
            )
    return None


def _alternative_payload(
    candidate: SearchResultItem, *, calorie_estimate: int | None, cheaper: bool, lower_calorie: bool
) -> dict[str, Any]:
    return {
        "name": candidate.name,
        "unit_price_paise": candidate.unit_price_paise,
        "calorie_estimate": calorie_estimate,
        "redirect_url": candidate.redirect_url,
        "cheaper": cheaper,
        "lower_calorie": lower_calorie,
    }


def _suggestion_entry(
    *,
    platform: str,
    key: str,
    name: str,
    avg_unit_price_paise: int | None,
    calorie_estimate: int | None,
    alternative: dict[str, Any],
) -> dict[str, Any]:
    return {
        "platform": platform,
        "frequent_item": {
            "key": key,
            "name": name,
            "avg_unit_price_paise": avg_unit_price_paise,
            "calorie_estimate": calorie_estimate,
        },
        "alternative": alternative,
    }
