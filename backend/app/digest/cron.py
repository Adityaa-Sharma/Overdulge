"""Weekly digest cron entrypoint (FR-5, ADR-0007 §3;
docs/architecture/features/6-budgeting.md §6).

`run_weekly_digest` enumerates every distinct `user_id` with a
current-month `budgets` row across *all* users (service-role — no
browser-present user JWT exists for a cron trigger, same shape as
`sync/cron.py::run_daily_sync`; ADR-0002's service-role confinement list
is extended to `digest/` by ADR-0007 §3). For each such user it fetches
that user's `orders`/`order_items`/`budgets` — service-role, but every
query is explicitly scoped by `user_id = :id` (the id comes from the
trusted enumeration query itself, never from external input), so RLS
isn't the safety mechanism here either, same argument ADR-0002 already
makes for `sync/`/`oauth/` — then renders and sends the digest. One
recipient's failure (missing email, provider error, ...) is captured in
that user's `DigestResult` and never stops the run for the rest, same
"one bad account must not stop the loop" discipline as
`sync/cron.py::run_sync_for_account`.

Deployment note: same as `sync/cron.py::run_daily_sync` — this module is
the cron *entrypoint function*, not the trigger wiring itself. The repo
migrated off Cloudflare Workers to Google Cloud Run (commit `8afbc9a`,
see #52/#103) before this task landed, so there is no `wrangler.toml`
`[triggers] crons` array or `Default.scheduled()` dispatch left to extend
per ADR-0007 §3's original design — both files were removed by that
migration. Wiring an external weekly trigger (GitHub Actions cron or
Cloud Scheduler hitting an HTTP endpoint) is left to a follow-up task,
same deferral already accepted for FR-2's daily sync trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from app.core import budgets as budgets_core
from app.core import db
from app.core import orders as orders_core
from app.core.safe_log import log_event
from app.digest import render, send


@dataclass
class DigestResult:
    user_id: str
    success: bool
    error: str | None = None


def _current_month_start(now: datetime) -> str:
    return date(now.year, now.month, 1).isoformat()


def run_weekly_digest(
    *, transport: httpx.BaseTransport | None = None, now: datetime | None = None
) -> list[DigestResult]:
    """Cron entrypoint (AC-4): sends the weekly digest to every user with
    at least one budget cap set this month. A user with zero `budgets`
    rows for the current month is never enumerated, so they receive no
    email (AC-5 — the digest is the only send path in FR-5).

    `now` defaults to the real current time; tests pass an explicit value
    for determinism, same discipline `analytics/aggregate.py` uses (ADR-0006).
    """
    now = now or datetime.now(UTC)
    client = db.service_role_client(transport=transport)
    try:
        month = _current_month_start(now)
        month_budget_rows = client.select("budgets", filters={"month": f"eq.{month}"})
        user_ids = sorted({row["user_id"] for row in month_budget_rows})
        return [
            _send_digest_for_user(client, user_id, month=month, now=now, transport=transport)
            for user_id in user_ids
        ]
    finally:
        client.close()


def _send_digest_for_user(
    client: db.PostgrestClient,
    user_id: str,
    *,
    month: str,
    now: datetime,
    transport: httpx.BaseTransport | None,
) -> DigestResult:
    try:
        budget_rows = budgets_core.list_budgets(client, user_id=user_id, month=month)
        order_rows = orders_core.list_orders(client, user_id=user_id, is_cancelled=False)
        order_item_rows = orders_core.list_order_items_for_orders(
            client, order_ids=[order["id"] for order in order_rows]
        )
        html = render.render_digest_html(order_rows, order_item_rows, budget_rows, now=now)

        email = send.resolve_user_email(user_id, transport=transport)
        if email is None:
            raise send.DigestSendError("user has no email on record")

        send.send_digest_email(
            to_email=email,
            subject="Your weekly Overdulge digest",
            html=html,
            transport=transport,
        )
    except Exception as exc:  # noqa: BLE001 -- one bad recipient must not stop the run
        log_event(
            "error",
            "weekly digest failed for user",
            user_id=user_id,
            error_type=type(exc).__name__,
        )
        return DigestResult(user_id=user_id, success=False, error=str(exc))

    return DigestResult(user_id=user_id, success=True)
