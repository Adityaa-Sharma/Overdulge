"""Grounded budget cut-suggestions route (FR-5, BRD §5;
docs/architecture/features/6-budgeting.md §5; ADR-0007 §1-§2). Kept as its
own route, separate from `GET /api/v1/budgets` (ADR-0007 §2), so a slow or
failing LLM round-trip never blocks the (fast, DB-only) progress numbers
from rendering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from app.analytics.aggregate import budget_progress
from app.api.budgets import _normalize_month
from app.core import budgets as budgets_core
from app.core import orders as orders_core
from app.core.auth import AuthedUser, get_current_user
from app.core.db import user_client
from app.llm import budget_suggestions

router = APIRouter()


@router.get("/budgets/suggestions")
async def get_budget_suggestions(
    month: str, user: AuthedUser = Depends(get_current_user)
) -> dict[str, Any]:
    normalized_month = _normalize_month(month)
    client = user_client(user.jwt)
    try:
        budget_rows = budgets_core.list_budgets(
            client, user_id=user.user_id, month=normalized_month
        )
        order_rows = orders_core.list_orders(client, user_id=user.user_id, is_cancelled=False)
        order_item_rows = orders_core.list_order_items_for_orders(
            client, order_ids=[order["id"] for order in order_rows]
        )
    finally:
        client.close()

    progress_rows = budget_progress(order_rows, order_item_rows, budget_rows, now=datetime.now(UTC))
    suggestions = budget_suggestions.generate_suggestions(user.jwt, progress_rows, normalized_month)
    return {"suggestions": suggestions}
