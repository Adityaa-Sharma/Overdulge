"""Calorie eligibility + reference-grounded estimation (FR-6; ADR-0008 §1-§2:
docs/architecture/decisions/0008-calorie-estimation-grounding-and-tone-safety.md).

`estimate_calories` grounds every estimate in
`llm/data/indian_nutrition_reference.json` (curated Indian dish/packaged-item
kcal-per-serving data) included as prompt context, rather than trusting model
recall (NFR-4). It never guesses: unparseable model output, or a value
outside `(0, MAX_PLAUSIBLE_KCAL]`, is discarded and the caller stores `NULL`.
Takes just an item name — no platform/category — so it works identically for
an already-synced `order_items.name` and an ephemeral live search-result name
(#41 reuses this verbatim, per ADR-0004).

`is_calorie_eligible` decides *whether* to call `estimate_calories` at all;
it is a pure function over `order_items.platform`/`category` with no schema
change (ADR-0008 §2) — callers must check eligibility first, this module
does not call itself.

Out of scope here: the ingest-time hook that calls these from sync, the
weekly blurb, `tone_guard.py` (all separate tasks).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.llm.agent import _default_chat_model

MAX_PLAUSIBLE_KCAL = 5000

READY_TO_EAT_CATEGORIES: set[str] = {
    "snacks",
    "beverages",
    "bakery",
    "ice-cream",
    "frozen desserts",
    "ready-to-eat",
}

_INSTAMART_LIKE_PLATFORMS = frozenset({"swiggy_instamart", "zepto"})

_REFERENCE_PATH = Path(__file__).resolve().parent / "data" / "indian_nutrition_reference.json"
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def is_calorie_eligible(platform: str, category: str | None) -> bool:
    """FR-6.1's "unless clearly ready-to-eat": `swiggy_food` is always
    eligible; `swiggy_instamart`/`zepto` only if `category` (lowercased) is
    on `READY_TO_EAT_CATEGORIES`. Anything else — including `category is
    None` or an unrecognized platform — defaults to ineligible.
    """
    if platform == "swiggy_food":
        return True
    if platform in _INSTAMART_LIKE_PLATFORMS:
        return category is not None and category.lower() in READY_TO_EAT_CATEGORIES
    return False


@lru_cache
def _reference_context() -> str:
    entries = json.loads(_REFERENCE_PATH.read_text())
    lines = (f"- {entry['name']}: {entry['kcal_per_serving']} kcal/serving" for entry in entries)
    return "\n".join(lines)


def _prompt(item_name: str) -> str:
    return (
        "You are estimating calories for an Indian food order item, using "
        "only the reference data below — map the item to the closest "
        "matching reference entries and estimate its total calories from "
        "them.\n\n"
        f"Reference data (kcal per typical serving):\n{_reference_context()}\n\n"
        f'Item: "{item_name}"\n\n'
        "Respond with only the estimated calories as a single integer "
        "number (kcal) for one serving of this item — no words, units, or "
        "punctuation."
    )


def _parse_kcal(text: str) -> int | None:
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    value = float(match.group())
    if not (0 < value <= MAX_PLAUSIBLE_KCAL):
        return None
    return round(value)


def estimate_calories(item_name: str, *, chat_model: BaseChatModel | None = None) -> int | None:
    """Maps `item_name` to an estimated kcal figure grounded in
    `llm/data/indian_nutrition_reference.json`. Returns `None` — never a
    guess — when the model's response is unparseable or outside
    `(0, MAX_PLAUSIBLE_KCAL]`.
    """
    model = chat_model if chat_model is not None else _default_chat_model()
    response = model.invoke([HumanMessage(content=_prompt(item_name))])
    text = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_kcal(text)
