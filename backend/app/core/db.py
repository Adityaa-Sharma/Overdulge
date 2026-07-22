"""PostgREST HTTP client wrapper — service-role and user-JWT-forwarding
modes (ADR-0002: docs/architecture/decisions/0002-supabase-access-pattern.md).

No table-specific code lives here — generic select/insert/upsert/delete
helpers over PostgREST's REST conventions only.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings


class PostgrestError(RuntimeError):
    """A PostgREST request returned a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"PostgREST request failed ({status_code}): {body}")
        self.status_code = status_code
        self.body = body


class PostgrestClient:
    """Thin wrapper over a PostgREST REST API (`/rest/v1/<table>`)."""

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/rest/v1",
            headers=headers,
            transport=transport,
        )

    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        params = {"select": columns, **(filters or {})}
        response = self._client.get(f"/{table}", params=params)
        _raise_for_status(response)
        return response.json()

    def insert(
        self, table: str, rows: list[dict[str, Any]] | dict[str, Any]
    ) -> list[dict[str, Any]]:
        response = self._client.post(
            f"/{table}", json=rows, headers={"Prefer": "return=representation"}
        )
        _raise_for_status(response)
        return response.json()

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]] | dict[str, Any],
        *,
        on_conflict: str,
    ) -> list[dict[str, Any]]:
        response = self._client.post(
            f"/{table}",
            json=rows,
            params={"on_conflict": on_conflict},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        _raise_for_status(response)
        return response.json()

    def delete(self, table: str, *, filters: dict[str, str]) -> list[dict[str, Any]]:
        response = self._client.request(
            "DELETE",
            f"/{table}",
            params=filters,
            headers={"Prefer": "return=representation"},
        )
        _raise_for_status(response)
        return response.json()

    def close(self) -> None:
        self._client.close()


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise PostgrestError(response.status_code, response.text)


def service_role_client(*, transport: httpx.BaseTransport | None = None) -> PostgrestClient:
    """Bypasses RLS. Import only from `sync/` and `oauth/` — see ADR-0002.

    Every call site using this client must scope its own queries with an
    explicit `user_id` filter; the database will not do it.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase service-role credentials are not configured")
    return PostgrestClient(
        settings.supabase_url,
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        },
        transport=transport,
    )


def user_client(jwt: str, *, transport: httpx.BaseTransport | None = None) -> PostgrestClient:
    """Forwards the caller's Supabase JWT; RLS enforces isolation. See ADR-0002."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise RuntimeError("Supabase anon credentials are not configured")
    return PostgrestClient(
        settings.supabase_url,
        headers={
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {jwt}",
        },
        transport=transport,
    )
