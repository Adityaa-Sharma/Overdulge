"""Calorie eligibility + reference-grounded estimation (FR-6; ADR-0008 §1-§2:
docs/architecture/decisions/0008-calorie-estimation-grounding-and-tone-safety.md).

`is_calorie_eligible` decides, from existing `orders`/`order_items` columns
only (no schema change), whether an item should ever get an estimate:
`swiggy_food` items always qualify; `swiggy_instamart`/`zepto` items qualify
only if their `category` is on the maintained `READY_TO_EAT_CATEGORIES`
allow-list. `estimate_calories` maps a bare item name to a kcal figure
grounded in `llm/data/indian_nutrition_reference.json` — included as prompt
context so the model maps to a reference entry rather than recalling a
number from training data — and returns `None` (never a guess) for
unparseable or implausible output. Both functions are the concrete
interface FR-7's #41 imports verbatim (ADR-0004) — do not fork either one
for a second caller.

`generate_weekly_blurb` follows the same fait-accompli pattern as
`llm/budget_suggestions.py` (ADR-0003, reused unmodified from `llm/agent.py`)
and adds `llm/tone_guard.py::check_tone` as a second guard, per ADR-0008 §5 —
either guard failing falls back to `tone_guard.FALLBACK_BLURB`.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.llm import tone_guard
from app.llm.agent import _default_chat_model, format_inr, generate_grounded_sentence

MAX_PLAUSIBLE_KCAL = 5000

READY_TO_EAT_CATEGORIES: set[str] = {
    "snacks",
    "beverages",
    "bakery",
    "ice-cream",
    "frozen desserts",
    "ready-to-eat",
}

_REFERENCE_PATH = Path(__file__).parent / "data" / "indian_nutrition_reference.json"

_KCAL_RE = re.compile(r"-?\d+(?:\.\d+)?")


def is_calorie_eligible(platform: str, category: str | None) -> bool:
    """True when an order item should ever receive a calorie estimate.

    `swiggy_food` items are always eligible (every Food line is, by
    definition, ready-to-eat). `swiggy_instamart`/`zepto` items are eligible
    only if `category` (lowercased) is on `READY_TO_EAT_CATEGORIES`; `None`
    or an unrecognized category is not eligible — the default is exclusion
    (FR-6.1 "unless clearly ready-to-eat").
    """
    if platform == "swiggy_food":
        return True
    if category is None:
        return False
    return category.lower() in READY_TO_EAT_CATEGORIES


@lru_cache
def _reference_entries() -> tuple[dict[str, Any], ...]:
    with _REFERENCE_PATH.open(encoding="utf-8") as handle:
        return tuple(json.load(handle))


def _reference_context() -> str:
    return "\n".join(
        f"- {entry['name']}: {entry['kcal_per_serving']} kcal per serving"
        for entry in _reference_entries()
    )


def _prompt(item_name: str) -> str:
    return (
        "Indian nutrition reference (kcal per typical serving):\n"
        f"{_reference_context()}\n\n"
        f'Estimate the calories in one serving of "{item_name}" by mapping it '
        "to the closest entries in the reference above. Reply with only the "
        "integer number of kilocalories for one serving — no words, no units."
    )


def _parse_kcal(text: str) -> int | None:
    match = _KCAL_RE.search(text)
    if match is None:
        return None
    value = round(float(match.group()))
    if value <= 0 or value > MAX_PLAUSIBLE_KCAL:
        return None
    return value


def estimate_calories(item_name: str, *, chat_model: BaseChatModel | None = None) -> int | None:
    """Maps `item_name` to a kcal estimate grounded in the Indian nutrition
    reference dataset. Takes just the item name — no platform/category, the
    caller decides eligibility via `is_calorie_eligible` before calling this
    — so the same function serves both an already-synced `order_items.name`
    and an ephemeral live search-result name (#41, ADR-0004).

    Returns `None`, never a guess, when the model's reply is unparseable or
    outside `(0, MAX_PLAUSIBLE_KCAL]`.
    """
    model = chat_model if chat_model is not None else _default_chat_model()
    response = model.invoke([HumanMessage(content=_prompt(item_name))])
    text = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_kcal(text)


def _weekly_blurb_prompt(fact_summary: str) -> str:
    return (
        f"You computed: {fact_summary}.\n"
        "Write exactly one short, playful sentence about the user's ordering "
        "habits or spending this week, referencing the figures above. Stay "
        "playful about ordering habits and money only — never mention body "
        "weight, dieting, or health. Use only the figures above — do not "
        "compute, restate, or guess any other number."
    )


def generate_weekly_blurb(
    *,
    week_kcal_estimate: int,
    week_order_count: int,
    week_spend_paise: int,
    chat_model: BaseChatModel | None = None,
) -> str:
    """One playful sentence about this week's ordering habits/spending,
    grounded in the already-computed figures (fait-accompli prompt, ADR-0003
    pattern reused from `llm/agent.py`). Runs the numeric-grounding guard
    then `tone_guard.check_tone`; either failing falls back to
    `tone_guard.FALLBACK_BLURB` (ADR-0008 §5) — never body/weight/dieting
    language, never a number the caller didn't hand in.
    """
    model = chat_model if chat_model is not None else _default_chat_model()
    noun = "order" if week_order_count == 1 else "orders"
    spend = format_inr(week_spend_paise)
    fact_summary = (
        f"{week_order_count} {noun} this week, {spend} spent, ~{week_kcal_estimate} kcal estimated"
    )
    allowed_values: list[object] = [week_order_count, spend, week_kcal_estimate]
    sentence = generate_grounded_sentence(
        model,
        _weekly_blurb_prompt(fact_summary),
        allowed_values,
        fallback=tone_guard.FALLBACK_BLURB,
    )
    return sentence if tone_guard.check_tone(sentence) else tone_guard.FALLBACK_BLURB
