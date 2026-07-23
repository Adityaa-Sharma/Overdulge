"""CRUD helpers for `budgets` (BRD §5, FR-5).

No business logic here — progress computation against `orders`/`order_items`
lives in the analytics layer, not this module. Callers pass in the client
matching their trust level; see ADR-0002.
"""

from __future__ import annotations

from typing import Any

from app.core.db import PostgrestClient


def list_budgets(client: PostgrestClient, *, user_id: str, month: str) -> list[dict[str, Any]]:
    return client.select(
        "budgets",
        filters={"user_id": f"eq.{user_id}", "month": f"eq.{month}"},
    )


def upsert_budget(
    client: PostgrestClient,
    *,
    user_id: str,
    month: str,
    category: str | None,
    cap_paise: int,
) -> dict[str, Any]:
    row = {
        "user_id": user_id,
        "month": month,
        "category": category,
        "cap_paise": cap_paise,
    }
    on_conflict = "user_id,month,category" if category is not None else "user_id,month"
    rows = client.upsert("budgets", row, on_conflict=on_conflict)
    return rows[0]


def delete_budget(client: PostgrestClient, *, id: str) -> None:
    client.delete("budgets", filters={"id": f"eq.{id}"})
