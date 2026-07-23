"""Account-linking routes (FR-1.2/FR-1.3, ADR-0001): start and callback for
the generic OAuth engine, plus link status and unlink. `start` and the
status/unlink routes are authenticated; `callback` is necessarily public —
the platform's auth server redirects the raw browser there, and its only
trust anchor is the opaque `state` value (see ADR-0001).

Status/unlink go through `db.user_client` (user-JWT-forwarding, ADR-0002) so
RLS scopes every row to the caller — never the service-role client used by
`oauth/engine.py`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from app.core import linked_accounts
from app.core.auth import AuthedUser, get_current_user
from app.core.config import get_settings
from app.core.db import user_client
from app.oauth import engine
from app.oauth.platforms import swiggy, zepto

router = APIRouter()

_PLATFORMS = {"swiggy": swiggy.CONFIG, "zepto": zepto.CONFIG}


def _platform_config(platform: str) -> engine.PlatformConfig:
    config = _PLATFORMS.get(platform)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown platform: {platform}"
        )
    return config


@router.post("/links/{platform}/start")
async def start_link(platform: str, user: AuthedUser = Depends(get_current_user)) -> dict[str, str]:
    config = _platform_config(platform)
    result = engine.start_link(config, user_id=user.user_id)
    return {"authorization_url": result.authorization_url}


@router.get("/links/{platform}/callback")
async def link_callback(
    platform: str, state: str, code: str | None = None, error: str | None = None
) -> RedirectResponse:
    """`code`/`error` are mutually exclusive per RFC 6749 §4.1.2/§4.1.2.1: the
    auth server redirects here with `code` on success, or `error` (no `code`)
    when the user declines/cancels or the server itself fails. Either way the
    browser must land on a redirect, never a raw 4xx/5xx.
    """
    config = _platform_config(platform)
    settings = get_settings()
    if not settings.frontend_settings_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="FRONTEND_SETTINGS_URL is not configured",
        )

    linked_ok = (
        code is not None
        and error is None
        and engine.handle_callback(config, code=code, state=state)
    )
    query = urlencode({"linked": platform, "status": "ok" if linked_ok else "error"})
    return RedirectResponse(
        url=f"{settings.frontend_settings_url}?{query}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/links")
async def list_links(user: AuthedUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Link status per supported platform. Never includes `tokens_encrypted`
    or any token field — the response is built from an explicit allowlist of
    keys, not by forwarding the DB row.
    """
    client = user_client(user.jwt)
    try:
        rows = linked_accounts.list_linked_accounts(client, user_id=user.user_id)
    finally:
        client.close()

    rows_by_platform = {row["platform"]: row for row in rows}
    return [
        {
            "platform": platform,
            "linked": platform in rows_by_platform,
            "linked_at": rows_by_platform.get(platform, {}).get("linked_at"),
        }
        for platform in _PLATFORMS
    ]


@router.delete("/links/{platform}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(platform: str, user: AuthedUser = Depends(get_current_user)) -> None:
    """Deletes the caller's `linked_accounts` row for `platform`, including
    the encrypted tokens. Idempotent — unlinking an already-unlinked (but
    known) platform still returns 204.
    """
    _platform_config(platform)
    client = user_client(user.jwt)
    try:
        linked_accounts.delete_linked_account(client, user_id=user.user_id, platform=platform)
    finally:
        client.close()
