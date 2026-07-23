"""Generic OAuth 2.1 + PKCE(S256) + Dynamic Client Registration engine
(ADR-0001). Platform-specific knowledge is confined to the `PlatformConfig`
values supplied by `oauth/platforms/*.py` — this module knows nothing about
any particular platform.

Every public function takes an optional `transport` (an `httpx.BaseTransport`)
so tests can substitute an `httpx.MockTransport` for the platform's
authorization server, matching the convention `core/db.py` uses for
PostgREST.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from app.core import crypto, db, linked_accounts
from app.core.config import get_settings

_PENDING_LINK_TTL = timedelta(minutes=10)
_STATE_BYTES = 32  # 256 bits, per ADR-0001 ("state must be unguessable")


class OAuthError(RuntimeError):
    """A flow step (metadata discovery, DCR, or token exchange) failed."""


@dataclass(frozen=True)
class PlatformConfig:
    """Per-platform config supplied by `oauth/platforms/*.py`."""

    name: str
    issuer: str
    scopes: tuple[str, ...]
    mcp_base_url: str


@dataclass(frozen=True)
class AuthorizationRequest:
    authorization_url: str
    state: str


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: str | None


def start_link(
    config: PlatformConfig,
    *,
    user_id: str,
    transport: httpx.BaseTransport | None = None,
) -> AuthorizationRequest:
    """Persists PKCE + state server-side and returns the authorization URL
    the frontend should redirect the browser to.
    """
    db_client = db.service_role_client()
    try:
        with httpx.Client(transport=transport) as http:
            metadata = _fetch_metadata(http, config)
            client_id, _ = _get_or_register_client(db_client, http, config, metadata)

        code_verifier, code_challenge = _generate_pkce_pair()
        state = _generate_state()
        expires_at = datetime.now(UTC) + _PENDING_LINK_TTL

        linked_accounts.upsert_pending_link(
            db_client,
            user_id=user_id,
            platform=config.name,
            code_verifier=code_verifier,
            state=state,
            expires_at=expires_at.isoformat(),
        )
    finally:
        db_client.close()

    authorization_endpoint = metadata.get("authorization_endpoint")
    if not authorization_endpoint:
        raise OAuthError("platform metadata is missing authorization_endpoint")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _redirect_uri(config.name),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if config.scopes:
        params["scope"] = " ".join(config.scopes)

    return AuthorizationRequest(
        authorization_url=f"{authorization_endpoint}?{urlencode(params)}", state=state
    )


def handle_callback(
    config: PlatformConfig,
    *,
    code: str,
    state: str,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """Exchanges `code` for tokens and upserts `linked_accounts`.

    Returns True on success. Returns False for every expected failure mode
    (unknown/expired/mismatched-platform state, or a failed token exchange)
    instead of raising — `linked_accounts` is left untouched in that case,
    and the caller redirects with `status=error`.
    """
    db_client = db.service_role_client()
    try:
        pending = linked_accounts.get_pending_link_by_state(db_client, state=state)
        if pending is None or pending["platform"] != config.name:
            return False

        user_id = pending["user_id"]
        expires_at = datetime.fromisoformat(pending["expires_at"])
        if expires_at <= datetime.now(UTC):
            linked_accounts.delete_pending_link(db_client, user_id=user_id, platform=config.name)
            return False

        try:
            with httpx.Client(transport=transport) as http:
                metadata = _fetch_metadata(http, config)
                client_id, client_secret = _get_or_register_client(
                    db_client, http, config, metadata
                )
                token_set = _exchange_code(
                    http,
                    config,
                    metadata,
                    client_id,
                    client_secret,
                    code,
                    pending["code_verifier"],
                )
        except OAuthError:
            linked_accounts.delete_pending_link(db_client, user_id=user_id, platform=config.name)
            return False

        linked_accounts.upsert_linked_account(
            db_client,
            user_id=user_id,
            platform=config.name,
            tokens_encrypted=_encode_tokens(token_set),
        )
        linked_accounts.delete_pending_link(db_client, user_id=user_id, platform=config.name)
        return True
    finally:
        db_client.close()


def refresh_tokens(
    config: PlatformConfig,
    *,
    refresh_token: str,
    transport: httpx.BaseTransport | None = None,
) -> TokenSet:
    """Rotates a refresh token against the platform's token endpoint. Callers
    are responsible for re-encrypting and upserting `linked_accounts`.
    """
    db_client = db.service_role_client()
    try:
        with httpx.Client(transport=transport) as http:
            metadata = _fetch_metadata(http, config)
            client_id, client_secret = _get_or_register_client(db_client, http, config, metadata)

            token_endpoint = metadata.get("token_endpoint")
            if not token_endpoint:
                raise OAuthError("platform metadata is missing token_endpoint")

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
            if client_secret:
                data["client_secret"] = client_secret

            response = http.post(token_endpoint, data=data)
            if response.status_code >= 400:
                raise OAuthError(f"token refresh failed ({response.status_code})")

            token_set = _token_set_from_response(response.json())
    finally:
        db_client.close()

    if token_set.refresh_token is None:
        # Some servers don't rotate the refresh token on every use.
        token_set = TokenSet(
            access_token=token_set.access_token,
            refresh_token=refresh_token,
            expires_at=token_set.expires_at,
        )
    return token_set


def decode_tokens(tokens_encrypted: str) -> TokenSet:
    """Decrypts a `linked_accounts.tokens_encrypted` value into a `TokenSet`."""
    payload = json.loads(crypto.decrypt(tokens_encrypted))
    return TokenSet(**payload)


def _redirect_uri(platform: str) -> str:
    settings = get_settings()
    if not settings.backend_base_url:
        raise OAuthError("BACKEND_BASE_URL is not configured")
    return f"{settings.backend_base_url.rstrip('/')}/api/v1/links/{platform}/callback"


def _discovery_url(issuer: str) -> str:
    """RFC 8414 §3.1 well-known construction for an issuer with a path."""
    parsed = urlsplit(issuer)
    suffix = parsed.path.rstrip("/")
    path = f"/.well-known/oauth-authorization-server{suffix}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _fetch_metadata(http: httpx.Client, config: PlatformConfig) -> dict[str, Any]:
    response = http.get(_discovery_url(config.issuer))
    if response.status_code >= 400:
        raise OAuthError(f"authorization-server metadata discovery failed ({response.status_code})")
    return response.json()


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _generate_state() -> str:
    return _b64url(os.urandom(_STATE_BYTES))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    return datetime.fromisoformat(expires_at) <= datetime.now(UTC)


def _get_or_register_client(
    db_client: db.PostgrestClient,
    http: httpx.Client,
    config: PlatformConfig,
    metadata: dict[str, Any],
) -> tuple[str, str | None]:
    """Reuses the cached DCR registration for `config.name` unless it is
    missing or expired, in which case it registers a new one (ADR-0001: DCR
    is per-deployment, not per-user — never re-register on every link).
    """
    existing = linked_accounts.get_oauth_client(db_client, platform=config.name)
    if existing is not None and not _is_expired(existing.get("expires_at")):
        client_secret = (
            crypto.decrypt(existing["client_secret_encrypted"]).decode()
            if existing["client_secret_encrypted"]
            else None
        )
        return existing["client_id"], client_secret

    registration_endpoint = metadata.get("registration_endpoint")
    if not registration_endpoint:
        raise OAuthError("platform metadata is missing registration_endpoint")

    payload: dict[str, Any] = {
        "client_name": "Overdulge",
        "redirect_uris": [_redirect_uri(config.name)],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }
    if config.scopes:
        payload["scope"] = " ".join(config.scopes)

    response = http.post(registration_endpoint, json=payload)
    if response.status_code >= 400:
        raise OAuthError(f"dynamic client registration failed ({response.status_code})")

    registration = response.json()
    client_id = registration["client_id"]
    client_secret = registration.get("client_secret")
    expires_at = None
    if registration.get("client_secret_expires_at"):
        expires_at = datetime.fromtimestamp(
            registration["client_secret_expires_at"], tz=UTC
        ).isoformat()

    linked_accounts.upsert_oauth_client(
        db_client,
        platform=config.name,
        client_id=client_id,
        client_secret_encrypted=crypto.encrypt((client_secret or "").encode("utf-8")),
        expires_at=expires_at,
    )
    return client_id, client_secret


def _exchange_code(
    http: httpx.Client,
    config: PlatformConfig,
    metadata: dict[str, Any],
    client_id: str,
    client_secret: str | None,
    code: str,
    code_verifier: str,
) -> TokenSet:
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise OAuthError("platform metadata is missing token_endpoint")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(config.name),
        "code_verifier": code_verifier,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret

    response = http.post(token_endpoint, data=data)
    if response.status_code >= 400:
        raise OAuthError(f"token exchange failed ({response.status_code})")
    return _token_set_from_response(response.json())


def _token_set_from_response(payload: dict[str, Any]) -> TokenSet:
    expires_in = payload.get("expires_in")
    expires_at = None
    if expires_in is not None:
        expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at,
    )


def _encode_tokens(token_set: TokenSet) -> str:
    payload = {
        "access_token": token_set.access_token,
        "refresh_token": token_set.refresh_token,
        "expires_at": token_set.expires_at,
    }
    return crypto.encrypt(json.dumps(payload).encode("utf-8"))
