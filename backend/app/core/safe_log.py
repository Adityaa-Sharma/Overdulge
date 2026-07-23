"""Structured log-safety wrapper (ADR-0009 §2:
docs/architecture/decisions/0009-nfr1-nfr2-automated-safety-verification.md).

BRD §6 NFR-2 requires that no order data, tokens, or PII ever reach
application logs. A denylist of "forbidden" fields would silently miss the
next sensitive field someone adds; an allowlist fails safe instead — a new
field is invisible until someone deliberately adds it here.

`message` must always be a static string literal at the call site — never an
f-string or `.format()` call that interpolates request/order data into it.
That is enforced by review convention, not mechanically, because Python
cannot distinguish a literal from an interpolated string at runtime.

`user_id` is allowed because it is the RLS join key already used everywhere
in this codebase (a UUID), not itself PII — it lets sync/auth failures stay
correlatable to an account without naming what was in the account's data.
"""

from __future__ import annotations

import logging
from typing import Any

_ALLOWED_FIELDS = {
    "user_id",
    "platform",
    "route",
    "status_code",
    "duration_ms",
    "error_type",
    "sync_state_key",
    # `failure_reason` carries a short diagnostic built from status codes, our
    # own configuration names, and upstream *error codes* — never an upstream
    # response body, request payload, or anything derived from an order. It
    # exists because `error_type` alone ("OAuthError") cannot distinguish "the
    # platform refused our callback domain" from "the platform was down", and
    # that distinction is the difference between a fixable report and a shrug.
    # Producers are responsible for keeping third-party text out of it.
    "failure_reason",
}

_logger = logging.getLogger("overdulge")


def log_event(level: str, message: str, **fields: Any) -> None:
    """Emit `message` via stdlib `logging` with only allowlisted `fields`.

    Raises `ValueError` for any keyword argument outside `_ALLOWED_FIELDS`
    instead of silently dropping it — a silent drop would hide the bug
    instead of surfacing it in tests/review.
    """
    disallowed = fields.keys() - _ALLOWED_FIELDS
    if disallowed:
        raise ValueError(f"log_event received non-allowlisted field(s): {sorted(disallowed)}")
    _logger.log(getattr(logging, level.upper()), message, extra=fields)
