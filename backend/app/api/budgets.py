"""Budget CRUD + progress routes (FR-5, docs/architecture/features/6-budgeting.md
§3): create-or-replace a monthly cap, fetch caps + progress for a month, and
delete a cap. User-JWT-forwarding throughout (ADR-0002) — RLS scopes every
call, no explicit `user_id` filter is added here beyond what `core/budgets.py`
already requires for its own queries.

`GET` fetches `orders`/`order_items` the same way the dashboard route does
(ADR-0006 — unfiltered by date, `is_cancelled=eq.false` only) and lets
`analytics/aggregate.py`'s `budget_progress()` (#68) do the month-scoping in
Python, keyed off an explicit `now` built from the requested month so a past
month's progress is computed against that month, not today's.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.analytics import aggregate
from app.core import budgets as core_budgets
from app.core.auth import AuthedUser, get_current_user
from app.core.db import user_client

router = APIRouter()


class BudgetCreate(BaseModel):
    month: str
    category: str | None = None
    cap_paise: int = Field(gt=0)


def _parse_month(value: str) -> date:
    """Accepts 'YYYY-MM' or any 'YYYY-MM-DD'-shaped string and normalizes to
    the first of that calendar month — the caller never lands on the wrong
    `(user_id, month, category)` unique-index bucket by supplying a
    mid-month date.
    """
    parts = value.split("-")
    if len(parts) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid month: {value!r}",
        )
    try:
        return date(int(parts[0]), int(parts[1]), 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid month: {value!r}",
        ) from exc


@router.post("/budgets")
async def create_budget(
    payload: BudgetCreate, user: AuthedUser = Depends(get_current_user)
) -> dict[str, Any]:
    month = _parse_month(payload.month)
    client = user_client(user.jwt)
    try:
        try:
            return core_budgets.upsert_budget(
                client,
                user_id=user.user_id,
                month=month.isoformat(),
                category=payload.category,
                cap_paise=payload.cap_paise,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
    finally:
        client.close()


@router.get("/budgets")
async def get_budgets(month: str, user: AuthedUser = Depends(get_current_user)) -> dict[str, Any]:
    target_month = _parse_month(month)
    now = datetime(target_month.year, target_month.month, 1, tzinfo=UTC)

    client = user_client(user.jwt)
    try:
        budget_rows = core_budgets.list_budgets(
            client, user_id=user.user_id, month=target_month.isoformat()
        )
        orders = client.select("orders", filters={"is_cancelled": "eq.false"})
        order_ids = [order["id"] for order in orders]
        order_items = (
            client.select("order_items", filters={"order_id": f"in.({','.join(order_ids)})"})
            if order_ids
            else []
        )
    finally:
        client.close()

    progress = aggregate.budget_progress(orders, order_items, budget_rows, now=now)
    merged = [
        {"id": budget["id"], **row} for budget, row in zip(budget_rows, progress, strict=True)
    ]

    return {"month": f"{target_month.year:04d}-{target_month.month:02d}", "budgets": merged}


@router.delete("/budgets/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(id: str, user: AuthedUser = Depends(get_current_user)) -> None:
    client = user_client(user.jwt)
    try:
        core_budgets.delete_budget(client, id=id)
    finally:
        client.close()
