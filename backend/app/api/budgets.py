"""Budget CRUD + progress routes (FR-5, BRD §5;
docs/architecture/features/6-budgeting.md §3). User-JWT-forwarding
throughout (ADR-0002) — RLS scopes every call, so route code never adds an
explicit `user_id` filter beyond what `core/budgets.py`/`core/orders.py`
already do.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.analytics.aggregate import budget_progress
from app.core import budgets as budgets_core
from app.core import orders as orders_core
from app.core.auth import AuthedUser, get_current_user
from app.core.db import user_client

router = APIRouter()


class BudgetIn(BaseModel):
    month: str
    category: str | None = None
    cap_paise: int = Field(gt=0)


def _normalize_month(value: str) -> str:
    """Floors any `YYYY-MM` or `YYYY-MM-DD` string to the first of that
    calendar month, matching `budgets.month`'s always-first-of-month column
    (docs/architecture/features/6-budgeting.md §2/§3).
    """
    parts = value.split("-")
    try:
        year, month = int(parts[0]), int(parts[1])
        return date(year, month, 1).isoformat()
    except (IndexError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid month: {value!r}",
        ) from exc


@router.post("/budgets", status_code=status.HTTP_201_CREATED)
async def create_budget(
    body: BudgetIn, user: AuthedUser = Depends(get_current_user)
) -> dict[str, Any]:
    client = user_client(user.jwt)
    try:
        row = budgets_core.upsert_budget(
            client,
            user_id=user.user_id,
            month=_normalize_month(body.month),
            category=body.category,
            cap_paise=body.cap_paise,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    finally:
        client.close()
    return row


@router.get("/budgets")
async def get_budgets(month: str, user: AuthedUser = Depends(get_current_user)) -> dict[str, Any]:
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
    return {
        "month": normalized_month,
        "budgets": [
            {"id": budget["id"], **progress}
            for budget, progress in zip(budget_rows, progress_rows, strict=True)
        ],
    }


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget_id: str, user: AuthedUser = Depends(get_current_user)) -> None:
    client = user_client(user.jwt)
    try:
        budgets_core.delete_budget(client, id=budget_id)
    finally:
        client.close()
