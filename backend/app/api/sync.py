"""Sync API routes (FR-2.4/AC-2/AC-10, ADR-0005 §6): per-platform sync status
and the manual "Sync now" trigger. Both routes are user-JWT-forwarding
(ADR-0002) — `run_sync_for_account` (#52) is called with the caller's own
`linked_accounts` row, the same trust level `links.py` already uses for
account-scoped reads/writes.

`orders.platform` has three values (`swiggy_food`/`swiggy_instamart`/
`zepto`); `linked_accounts.platform` has two (`swiggy`/`zepto`). A manual
sync of either Swiggy surface resolves to the one underlying `swiggy` row
and, via `run_sync_for_account`, refreshes both sub-platforms under one lock
(ADR-0005 §4/§6).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import linked_accounts
from app.core.auth import AuthedUser, get_current_user
from app.core.db import user_client
from app.sync.cron import run_sync_for_account

router = APIRouter()

# Longer than any plausible real sync (ADR-0005 §4) — a lock older than this
# is treated as abandoned by a crashed run, not actually in-flight.
_STALE_LOCK_TIMEOUT = timedelta(minutes=10)

_INSTAMART_WINDOW_WARNING = (
    "Instamart's order history is only available for roughly the last 15 "
    "days — orders older than that may already be lost to future syncs."
)

# orders.platform (3 values, BRD §5) -> the linked_accounts.platform (2
# values, migration 0003) row that owns its tokens/sync_state.
_LINKED_ACCOUNT_PLATFORM = {
    "swiggy_food": "swiggy",
    "swiggy_instamart": "swiggy",
    "zepto": "zepto",
}


@router.get("/sync/status")
async def get_sync_status(user: AuthedUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    """One entry per `orders.platform` value the caller has actually linked;
    a platform with no `linked_accounts` row at all is omitted, not shown
    empty (ADR-0005 §6).
    """
    client = user_client(user.jwt)
    try:
        rows = linked_accounts.list_linked_accounts(client, user_id=user.user_id)
    finally:
        client.close()

    rows_by_platform = {row["platform"]: row for row in rows}
    entries = []
    for platform, linked_account_platform in _LINKED_ACCOUNT_PLATFORM.items():
        row = rows_by_platform.get(linked_account_platform)
        if row is None:
            continue
        orders_captured = (row.get("sync_state") or {}).get("orders_captured_last_run") or {}
        entries.append(
            {
                "platform": platform,
                "last_sync_at": row.get("last_sync_at"),
                "orders_captured_last_run": orders_captured.get(platform),
                "warning": _INSTAMART_WINDOW_WARNING if platform == "swiggy_instamart" else None,
            }
        )
    return entries


@router.post("/sync/{platform}")
async def trigger_sync(
    platform: str, user: AuthedUser = Depends(get_current_user)
) -> dict[str, Any]:
    """Runs a synchronous sync for the caller's linked account backing
    `platform`, guarded by `sync_state`'s in-flight lock (ADR-0005 §4): 409
    if a sync already claims the lock and hasn't gone stale, 404 if
    `platform` was never linked. Returns the account's `sync_state` after the
    run — the per-platform-keyed `orders_captured_last_run` shape ADR-0005
    §6 documents.
    """
    linked_account_platform = _LINKED_ACCOUNT_PLATFORM.get(platform)
    if linked_account_platform is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown platform: {platform}"
        )

    client = user_client(user.jwt)
    try:
        account = linked_accounts.get_linked_account(
            client, user_id=user.user_id, platform=linked_account_platform
        )
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{linked_account_platform} is not linked",
            )

        if _lock_is_held(account.get("sync_state") or {}):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a sync is already in progress for this account",
            )

        run_sync_for_account(client, account)

        refreshed = linked_accounts.get_linked_account(
            client, user_id=user.user_id, platform=linked_account_platform
        )
        return refreshed["sync_state"]
    finally:
        client.close()


def _lock_is_held(sync_state: dict[str, Any]) -> bool:
    if sync_state.get("status") != "syncing":
        return False
    syncing_since = sync_state.get("syncing_since")
    if not syncing_since:
        return False
    return datetime.now(UTC) - datetime.fromisoformat(syncing_since) < _STALE_LOCK_TIMEOUT
