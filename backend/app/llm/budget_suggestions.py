"""Grounded "where to cut" budget suggestions (FR-5; ADR-0007 §1-§2:
docs/architecture/decisions/0007-budget-progress-suggestions-and-digest.md).

For each `budget_progress()` row (`analytics/aggregate.py`, #68) with
`status in {"near", "over"}`, calls `llm/tools.py`'s `top_items_in_category`
for that row's category (or `None` for the overall cap) and builds a
fait-accompli prompt handing the model the already-computed cap, spend,
percentage, and item list as fixed facts, asking only for 2-3 sentences of
suggestion text referencing them. Reuses `llm/agent.py`'s post-generation
numeric-grounding guard unmodified (ADR-0003) — this module is a second
caller of that guard, not a second implementation of it. A "near"/"over"
category with no matching items (`order_items.category` not populated yet)
is skipped rather than prompted with nothing to reference.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel

from app.llm import tools
from app.llm.agent import _default_chat_model, format_inr, generate_grounded_sentence

Row = dict[str, Any]

_SUGGESTION_STATUSES = frozenset({"near", "over"})


def _month_end(month_start: str) -> str:
    year, month = int(month_start[:4]), int(month_start[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day).isoformat()


def _fact_summary(row: Row, items: list[Row]) -> tuple[str, list[object]]:
    label = row["category"] or "your overall budget"
    cap = format_inr(row["cap_paise"])
    spent = format_inr(row["spent_paise"])
    pct_value = round(row["pct"] * 100)
    item_totals = [format_inr(item["total_paise"]) for item in items]
    item_parts = [
        f"{item['name']} ({total})" for item, total in zip(items, item_totals, strict=True)
    ]
    summary = (
        f"{label} cap = {cap}, spent so far = {spent} ({pct_value}% of cap), "
        f"biggest contributors: {', '.join(item_parts)}"
    )
    allowed: list[object] = [cap, spent, pct_value, *item_totals]
    return summary, allowed


def _prompt(fact_summary: str) -> str:
    return (
        f"You computed: {fact_summary}.\n"
        "Write 2-3 short, natural sentences suggesting where the user could "
        "cut back, referencing the specific items above by name. Use only "
        "the figures above — do not compute, restate, or guess any other "
        "number."
    )


def generate_suggestions(
    jwt: str,
    progress_rows: list[Row],
    month: str,
    *,
    chat_model: BaseChatModel | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Builds a "where to cut" suggestion for every `progress_rows` entry at
    or past 80% of its cap (`status in {"near", "over"}`), grounded in that
    category's real top items for `month` (`YYYY-MM-DD`, first-of-month).
    Skips a category whose `top_items_in_category` result is empty rather
    than prompting the model with nothing to reference. Returns
    `[{"category": str | None, "text": str}, ...]`.
    """
    model = chat_model if chat_model is not None else _default_chat_model()
    start_date = month
    end_date = _month_end(month)

    suggestions: list[dict[str, Any]] = []
    for row in progress_rows:
        if row["status"] not in _SUGGESTION_STATUSES:
            continue
        items = tools.top_items_in_category(
            jwt, start_date, end_date, row["category"], transport=transport
        )
        if not items:
            continue
        fact_summary, allowed_values = _fact_summary(row, items)
        fallback = f"{fact_summary[0].upper()}{fact_summary[1:]}."
        text = generate_grounded_sentence(
            model, _prompt(fact_summary), allowed_values, fallback=fallback
        )
        suggestions.append({"category": row["category"], "text": text})
    return suggestions
