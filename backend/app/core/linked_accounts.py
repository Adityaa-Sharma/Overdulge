"""CRUD helpers for `linked_accounts`, `oauth_pending_links` and
`oauth_clients` (BRD §5, ADR-0001, ADR-0002).

No PostgrestClient is created here — callers pass in the client matching
their trust level (service-role for `oauth/`/`sync/`, user-JWT-forwarding
for `api/*` routes; see ADR-0002). Values are stored/returned encrypted;
callers are responsible for calling `core/crypto.py` around these helpers.
"""

from __future__ import annotations

from typing import Any

from app.core.db import PostgrestClient


def get_linked_account(
    client: PostgrestClient, *, user_id: str, platform: str
) -> dict[str, Any] | None:
    rows = client.select(
        "linked_accounts",
        filters={"user_id": f"eq.{user_id}", "platform": f"eq.{platform}"},
    )
    return rows[0] if rows else None


def list_linked_accounts(client: PostgrestClient, *, user_id: str) -> list[dict[str, Any]]:
    return client.select("linked_accounts", filters={"user_id": f"eq.{user_id}"})


def upsert_linked_account(
    client: PostgrestClient,
    *,
    user_id: str,
    platform: str,
    tokens_encrypted: str,
    sync_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "user_id": user_id,
        "platform": platform,
        "tokens_encrypted": tokens_encrypted,
        "sync_state": sync_state if sync_state is not None else {},
    }
    rows = client.upsert("linked_accounts", row, on_conflict="user_id,platform")
    return rows[0]


def delete_linked_account(client: PostgrestClient, *, user_id: str, platform: str) -> None:
    client.delete(
        "linked_accounts",
        filters={"user_id": f"eq.{user_id}", "platform": f"eq.{platform}"},
    )


def _account_filter(user_id: str, platform: str) -> dict[str, str]:
    return {"user_id": f"eq.{user_id}", "platform": f"eq.{platform}"}


def set_tokens(
    client: PostgrestClient, *, user_id: str, platform: str, tokens_encrypted: str
) -> dict[str, Any]:
    """Updates only `tokens_encrypted` (e.g. after `oauth/engine.py`'s
    `refresh_tokens`), leaving `sync_state`/`last_sync_at` untouched.

    A PATCH, not an upsert: the row always already exists (you cannot refresh
    a token for an account that was never linked), and an upsert omitting the
    NOT NULL columns would fail 23502 against the real database.
    """
    rows = client.update(
        "linked_accounts",
        {"tokens_encrypted": tokens_encrypted},
        filters=_account_filter(user_id, platform),
    )
    return rows[0]


def set_sync_status(
    client: PostgrestClient,
    *,
    user_id: str,
    platform: str,
    sync_state: dict[str, Any],
    status: str,
    syncing_since: str | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    """Writes `sync_state.status`/`syncing_since`/`last_error`, preserving
    `orders_captured_last_run` from the `sync_state` the caller already has
    in hand (e.g. from `list_linked_accounts`) — callers never hand-roll the
    jsonb merge themselves (ADR-0005 §4/§6).
    """
    updated_sync_state = {
        **sync_state,
        "status": status,
        "syncing_since": syncing_since,
        "last_error": last_error,
    }
    rows = client.update(
        "linked_accounts",
        {"sync_state": updated_sync_state},
        filters=_account_filter(user_id, platform),
    )
    return rows[0]


def record_sync_result(
    client: PostgrestClient,
    *,
    user_id: str,
    platform: str,
    sync_state: dict[str, Any],
    orders_captured: dict[str, int],
    synced_at: str,
) -> dict[str, Any]:
    """Records a *successful* sync: resets the lock to idle, clears
    `last_error`, records `orders_captured_last_run`, and writes
    `linked_accounts.last_sync_at` — the only path that ever writes that
    column (ADR-0005 §4: it is the single source of truth for "last
    successful sync").
    """
    updated_sync_state = {
        **sync_state,
        "status": "idle",
        "syncing_since": None,
        "last_error": None,
        "orders_captured_last_run": orders_captured,
    }
    rows = client.update(
        "linked_accounts",
        {"last_sync_at": synced_at, "sync_state": updated_sync_state},
        filters=_account_filter(user_id, platform),
    )
    return rows[0]


def upsert_pending_link(
    client: PostgrestClient,
    *,
    user_id: str,
    platform: str,
    code_verifier: str,
    state: str,
    expires_at: str,
) -> dict[str, Any]:
    row = {
        "user_id": user_id,
        "platform": platform,
        "code_verifier": code_verifier,
        "state": state,
        "expires_at": expires_at,
    }
    rows = client.upsert("oauth_pending_links", row, on_conflict="user_id,platform")
    return rows[0]


def get_pending_link_by_state(client: PostgrestClient, *, state: str) -> dict[str, Any] | None:
    rows = client.select("oauth_pending_links", filters={"state": f"eq.{state}"})
    return rows[0] if rows else None


def delete_pending_link(client: PostgrestClient, *, user_id: str, platform: str) -> None:
    client.delete(
        "oauth_pending_links",
        filters={"user_id": f"eq.{user_id}", "platform": f"eq.{platform}"},
    )


def get_oauth_client(client: PostgrestClient, *, platform: str) -> dict[str, Any] | None:
    rows = client.select("oauth_clients", filters={"platform": f"eq.{platform}"})
    return rows[0] if rows else None


def upsert_oauth_client(
    client: PostgrestClient,
    *,
    platform: str,
    client_id: str,
    client_secret_encrypted: str,
    expires_at: str | None = None,
) -> dict[str, Any]:
    row = {
        "platform": platform,
        "client_id": client_id,
        "client_secret_encrypted": client_secret_encrypted,
        "expires_at": expires_at,
    }
    rows = client.upsert("oauth_clients", row, on_conflict="platform")
    return rows[0]
