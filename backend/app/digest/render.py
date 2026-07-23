"""Weekly digest HTML content builder (FR-5, ADR-0007 §3;
docs/architecture/features/6-budgeting.md §6).

Pure function: fixture-shaped `orders`/`order_items`/`budgets` rows in
(same canonical schema every `analytics/aggregate.py` function takes),
a plain HTML string out. No I/O, no PostgREST/httpx imports, no
`datetime.now()` — "now" is an explicit parameter, same discipline
ADR-0006 established for the functions this module composes
(`spend_totals`, `budget_progress`).

Digest content is numbers-only (ADR-0007 §3) — no LLM call here, unlike
`llm/budget_suggestions.py`'s in-app cut-suggestions.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from app.analytics.aggregate import Row, budget_progress, spend_totals

_STATUS_LABELS = {"ok": "On track", "near": "Near cap", "over": "Over cap"}


def _format_inr(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def _budget_row_html(progress: dict[str, Any]) -> str:
    label = html.escape(progress["category"] or "Overall")
    return (
        "<tr>"
        f"<td>{label}</td>"
        f"<td>{_format_inr(progress['spent_paise'])}</td>"
        f"<td>{_format_inr(progress['cap_paise'])}</td>"
        f"<td>{progress['pct'] * 100:.0f}%</td>"
        f"<td>{_STATUS_LABELS[progress['status']]}</td>"
        "</tr>"
    )


def render_digest_html(
    orders: list[Row],
    order_items: list[Row],
    budgets: list[Row],
    *,
    now: datetime,
) -> str:
    """Builds the full digest email body: this-week + month-to-date spend
    (`spend_totals`) and progress for every cap the recipient has set this
    month (`budget_progress`). `budgets` should already be scoped to the
    recipient and the current month by the caller (`digest/cron.py`).
    """
    totals = spend_totals(orders, now=now)
    progress_rows = budget_progress(orders, order_items, budgets, now=now)

    if progress_rows:
        budget_section = (
            "<table>"
            "<thead><tr><th>Category</th><th>Spent</th><th>Cap</th>"
            "<th>%</th><th>Status</th></tr></thead>"
            f"<tbody>{''.join(_budget_row_html(row) for row in progress_rows)}</tbody>"
            "</table>"
        )
    else:
        budget_section = "<p>No budget caps set this month.</p>"

    return (
        "<html><body>"
        "<h1>Your weekly Overdulge digest</h1>"
        "<h2>Spend summary</h2>"
        f"<p>This week: {_format_inr(totals['this_week_paise']['combined'])}</p>"
        f"<p>Month to date: {_format_inr(totals['this_month_paise']['combined'])}</p>"
        "<h2>Budget status</h2>"
        f"{budget_section}"
        "</body></html>"
    )
