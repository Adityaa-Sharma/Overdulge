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
