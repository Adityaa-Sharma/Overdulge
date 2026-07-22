"""Supabase JWT verification (JWKS), FastAPI dependency that resolves the
authenticated user (SYSTEM.md `core/auth.py`; ADR-0002 needs the raw JWT
available for PostgREST user-JWT forwarding).
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

_JWT_AUDIENCE = "authenticated"
_JWT_ALGORITHMS = ["RS256", "ES256"]

_bearer_scheme = HTTPBearer(auto_error=False)
_jwks_client: jwt.PyJWKClient | None = None


@dataclass(frozen=True)
class AuthedUser:
    """The authenticated caller, resolved from a verified Supabase JWT."""

    user_id: str
    jwt: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_jwks_client() -> jwt.PyJWKClient:
    """Lazily builds the JWKS client once per process; caches keys by `kid`
    instead of fetching Supabase's JWKS endpoint on every request.
    """
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        if not settings.supabase_url:
            raise RuntimeError("SUPABASE_URL is not configured")
        jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthedUser:
    """FastAPI dependency: extracts `Authorization: Bearer <jwt>`, verifies it
    against Supabase's JWKS, and resolves `user_id` (the `sub` claim).
    Raises 401 on a missing, malformed, or expired/invalid token.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("missing bearer token")

    token = credentials.credentials
    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=_JWT_ALGORITHMS,
            audience=_JWT_AUDIENCE,
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized("invalid or expired token") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized("token missing subject claim")

    return AuthedUser(user_id=user_id, jwt=token)
