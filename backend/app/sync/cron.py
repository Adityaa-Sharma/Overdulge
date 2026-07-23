"""Sync orchestration core (issue #52, ADR-0005 §4/§6, BRD §3/FR-2.1).

`run_sync_for_account` is the single implementation of "sync one linked
account", used by both the daily cron loop (`run_daily_sync`, below) and the
manual "Sync now" route (a later task). It is the one place in FR-2 that
imports from `oauth/` (ADR-0005 §1 Context) — token refresh is delegated to
`oauth/engine.py`'s `refresh_tokens`/`decode_tokens`/`encode_tokens`.

This module never decides which MCP tool name to call or parses a tool's
response shape — that stays inside `mcp/adapters/*` (NFR-1, SYSTEM.md §2).
It passes `mcp/client.py`'s `call_tool` through to `swiggy_food`/
`swiggy_instamart` unmodified (their `McpCaller` type matches `call_tool`'s
signature exactly, by design — see those modules' docstrings) and passes a
transport straight through to `zepto`, which calls `call_tool` itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core import db, linked_accounts
from app.core.db import PostgrestClient
from app.core.safe_log import log_event
from app.mcp.adapters import swiggy_food, swiggy_instamart, zepto
from app.mcp.client import call_tool
from app.oauth import engine
from app.oauth.engine import PlatformConfig
from app.oauth.platforms import swiggy as swiggy_platform
from app.oauth.platforms import zepto as zepto_platform
from app.sync.normalize import upsert_orders

_PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    "swiggy": swiggy_platform.CONFIG,
    "zepto": zepto_platform.CONFIG,
}


@dataclass
class SyncResult:
    user_id: str
    platform: str
    success: bool
    orders_captured: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def run_sync_for_account(
    client: PostgrestClient,
    linked_account: dict[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> SyncResult:
    """Syncs one `linked_accounts` row end to end: refreshes the access
    token if it has expired, calls the relevant adapter(s), upserts the
    normalized orders, and updates the in-flight lock/state.

    Never raises — a single account's failure is captured in the returned
    `SyncResult` (and `sync_state.last_error`) so the cron loop can continue
    to the next account.
    """
    user_id = linked_account["user_id"]
    platform = linked_account["platform"]
    sync_state = linked_account.get("sync_state") or {}
    now = datetime.now(UTC).isoformat()

    linked_accounts.set_sync_status(
        client,
        user_id=user_id,
        platform=platform,
        sync_state=sync_state,
        status="syncing",
        syncing_since=now,
    )

    orders_captured: dict[str, int] = {}
    error: str | None = None
    try:
        access_token = _resolve_access_token(
            client,
            user_id=user_id,
            platform=platform,
            tokens_encrypted=linked_account["tokens_encrypted"],
            transport=transport,
        )
        for sub_platform, orders in _fetch_orders_by_sub_platform(
            platform, access_token, transport=transport
        ):
            result = upsert_orders(client, user_id=user_id, platform=sub_platform, orders=orders)
            orders_captured[sub_platform] = result.orders_upserted
    except Exception as exc:  # noqa: BLE001 -- one bad account must not stop the loop
        error = str(exc)
        log_event(
            "error",
            "sync failed for linked account",
            user_id=user_id,
            platform=platform,
            error_type=type(exc).__name__,
        )

    if error is None:
        linked_accounts.record_sync_result(
            client,
            user_id=user_id,
            platform=platform,
            sync_state=sync_state,
            orders_captured=orders_captured,
            synced_at=now,
        )
    else:
        linked_accounts.set_sync_status(
            client,
            user_id=user_id,
            platform=platform,
            sync_state=sync_state,
            status="idle",
            syncing_since=None,
            last_error=error,
        )

    return SyncResult(
        user_id=user_id,
        platform=platform,
        success=error is None,
        orders_captured=orders_captured,
        error=error,
    )


def run_daily_sync(*, transport: httpx.BaseTransport | None = None) -> list[SyncResult]:
    """Cron entrypoint (BRD §3/FR-2.1, AC-1): syncs every `linked_accounts`
    row across every user (service-role, ADR-0002), continuing past a
    single account's failure.

    Deployment note: this repo migrated off Cloudflare Workers to Google
    Cloud Run (see commit `8afbc9a`), so the `wrangler.toml` Cron Trigger
    named in ADR-0005 §4 no longer applies. Wiring an external daily
    trigger (GitHub Actions cron or Cloud Scheduler hitting an HTTP
    endpoint, per the current `docs/BRD.md`) is left to a follow-up task —
    it needs a workflow file and an HTTP route, neither of which is in this
    task's scope.
    """
    client = db.service_role_client(transport=transport)
    try:
        accounts = client.select("linked_accounts")
        return [run_sync_for_account(client, account, transport=transport) for account in accounts]
    finally:
        client.close()


def _resolve_access_token(
    client: PostgrestClient,
    *,
    user_id: str,
    platform: str,
    tokens_encrypted: str,
    transport: httpx.BaseTransport | None,
) -> str:
    token_set = engine.decode_tokens(tokens_encrypted)
    if not _is_expired(token_set.expires_at):
        return token_set.access_token

    config = _PLATFORM_CONFIGS[platform]
    refreshed = engine.refresh_tokens(
        config, refresh_token=token_set.refresh_token, transport=transport
    )
    linked_accounts.set_tokens(
        client,
        user_id=user_id,
        platform=platform,
        tokens_encrypted=engine.encode_tokens(refreshed),
    )
    return refreshed.access_token


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    return datetime.fromisoformat(expires_at) <= datetime.now(UTC)


def _fetch_orders_by_sub_platform(
    platform: str, access_token: str, *, transport: httpx.BaseTransport | None
) -> list[tuple[str, list[Any]]]:
    if platform == "swiggy":
        base_url = swiggy_platform.CONFIG.mcp_base_url

        def mcp_caller(
            base_url: str, access_token: str, tool_name: str, params: dict[str, Any]
        ) -> dict[str, Any]:
            return call_tool(base_url, access_token, tool_name, params, transport=transport)

        return [
            ("swiggy_food", swiggy_food.fetch_orders(mcp_caller, base_url, access_token)),
            (
                "swiggy_instamart",
                swiggy_instamart.fetch_orders(mcp_caller, base_url, access_token),
            ),
        ]
    if platform == "zepto":
        base_url = zepto_platform.CONFIG.mcp_base_url
        return [("zepto", zepto.fetch_orders(transport, base_url, access_token))]
    raise ValueError(f"unknown linked_accounts.platform {platform!r}")
