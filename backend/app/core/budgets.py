"""CRUD helpers for `budgets` (BRD §5, FR-5).

No business logic here — progress computation against `orders`/`order_items`
lives in the analytics layer, not this module. Callers pass in the client
matching their trust level; see ADR-0002.

The overall cap (`category is None`) is stored in the database under the
reserved sentinel below rather than true NULL — see
`backend/supabase/migrations/0007_create_budgets.sql` for why a NULL-based
design can't be upserted through PostgREST. Every function in this module
translates the sentinel back to `None` before returning a row, so callers
never see it.
"""

from __future__ import annotations

from typing import Any

from app.core.db import PostgrestClient

_OVERALL_CATEGORY = "__overall__"


def _to_db_category(category: str | None) -> str:
    if category == _OVERALL_CATEGORY:
        raise ValueError(f"category cannot be the reserved sentinel {_OVERALL_CATEGORY!r}")
    return _OVERALL_CATEGORY if category is None else category


def _from_db_row(row: dict[str, Any]) -> dict[str, Any]:
    if row["category"] == _OVERALL_CATEGORY:
        row = {**row, "category": None}
    return row


def list_budgets(client: PostgrestClient, *, user_id: str, month: str) -> list[dict[str, Any]]:
    rows = client.select(
        "budgets",
        filters={"user_id": f"eq.{user_id}", "month": f"eq.{month}"},
    )
    return [_from_db_row(row) for row in rows]


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
        "category": _to_db_category(category),
        "cap_paise": cap_paise,
    }
    rows = client.upsert("budgets", row, on_conflict="user_id,month,category")
    return _from_db_row(rows[0])


def delete_budget(client: PostgrestClient, *, id: str) -> None:
    client.delete("budgets", filters={"id": f"eq.{id}"})
