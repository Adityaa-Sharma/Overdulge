"""Digest delivery: Supabase GoTrue admin email lookup + Resend send
(FR-5, ADR-0007 §3).

Plain `httpx` calls to both providers' plain HTTP APIs — no provider SDK,
matching `core/db.py`'s Pyodide-compatibility reasoning for
`supabase-py` (issue #71 generalizes that same "assume no provider SDK's
native/non-pure-Python bindings work" rule to Resend's SDK too).

`RESEND_API_KEY`/`DIGEST_FROM_EMAIL` are read from `core/config.py`
(Worker-secret-only pattern, BRD §3 — same as every other secret).
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings


class DigestSendError(RuntimeError):
    """Recipient email lookup or the Resend send call failed."""


def resolve_user_email(user_id: str, *, transport: httpx.BaseTransport | None = None) -> str | None:
    """Looks up a user's email via Supabase's GoTrue Admin API
    (`GET /auth/v1/admin/users/{user_id}`, service-role key) — the
    canonical schema (BRD §5) has no email column on any user-scoped
    table. Returns `None` when the admin API reports no email on record,
    so the caller can skip that recipient rather than fail the whole run.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase service-role credentials are not configured")

    with httpx.Client(base_url=settings.supabase_url.rstrip("/"), transport=transport) as client:
        response = client.get(
            f"/auth/v1/admin/users/{user_id}",
            headers={
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            },
        )
    if response.status_code >= 400:
        raise DigestSendError(f"GoTrue admin user lookup failed ({response.status_code})")
    return response.json().get("email")


def send_digest_email(
    *,
    to_email: str,
    subject: str,
    html: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Sends one digest email via a single `POST
    https://api.resend.com/emails` call (ADR-0007 §3)."""
    settings = get_settings()
    if not settings.resend_api_key or not settings.digest_from_email:
        raise RuntimeError("Resend digest credentials are not configured")

    with httpx.Client(transport=transport) as client:
        response = client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.digest_from_email,
                "to": [to_email],
                "subject": subject,
                "html": html,
            },
        )
    if response.status_code >= 400:
        raise DigestSendError(f"Resend send failed ({response.status_code}): {response.text}")
