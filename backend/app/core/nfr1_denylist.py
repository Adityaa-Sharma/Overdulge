"""Single source of truth for NFR-1's mutating-tool denylist (ADR-0009 §1).

Reviewed whenever BRD §2 platform facts are revisited (BRD §6 NFR-1 AC-2).
Consumed only by `backend/scripts/check_nfr1_denylist.py` and its test —
never import this from request-handling code.
"""

from __future__ import annotations

# Each entry: mutating MCP tool name -> the platform/category it belongs to.
MUTATING_TOOL_DENYLIST: dict[str, str] = {
    "place_food_order": "swiggy_food",
    "confirm_order": "swiggy_food",
    "update_food_cart": "swiggy_food",
    "flush_food_cart": "swiggy_food",
    "apply_food_coupon": "swiggy_food",
    "checkout": "swiggy_instamart",
    "update_cart": "swiggy_instamart",
    "clear_cart": "swiggy_instamart",
    "create_order": "zepto",
    "create_online_payment_order": "payment",
    "create_upi_reserve_pay_order": "payment",
    "create_wallet_order": "payment",
    "add_saved_address": "account_mutation",
    "update_drop_zone": "account_mutation",
    "update_user_name": "account_mutation",
}
