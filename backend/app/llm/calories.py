"""Calorie eligibility + reference-grounded estimation (FR-6; ADR-0008:
docs/architecture/decisions/0008-calorie-estimation-grounding-and-tone-safety.md).

`estimate_calories` grounds every estimate against
`llm/data/indian_nutrition_reference.json` (included as prompt context)
rather than model recall (ADR-0008 §1): a response that is unparseable or
outside the plausible range is discarded as `None`, never guessed.
`is_calorie_eligible` decides which order items get an estimate at all
(ADR-0008 §2) — grocery items are excluded by default unless their category
is on the maintained ready-to-eat allow-list. `estimate_calories` takes only
an item name (ADR-0004) so #41's live-suggestion matching can call it
verbatim on an ephemeral search-result name, after applying this same
`is_calorie_eligible` to that result's platform/category.

Out of scope here: the ingest-time hook that calls this from sync, the
weekly blurb, and `tone_guard.py` (separate tasks).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.core.config import get_settings

MAX_PLAUSIBLE_KCAL = 5000

READY_TO_EAT_CATEGORIES: set[str] = {
    "snacks",
    "beverages",
    "bakery",
    "ice-cream",
    "frozen desserts",
    "ready-to-eat",
}

_REFERENCE_DATASET_PATH = Path(__file__).parent / "data" / "indian_nutrition_reference.json"

_KCAL_ONLY_RE = re.compile(r"-?\d+(?:\.\d+)?")


def is_calorie_eligible(platform: str, category: str | None) -> bool:
    """True when an order item should get a calorie estimate (ADR-0008 §2).

    `swiggy_food` items are always eligible — every Food line is
    ready-to-eat by definition. `swiggy_instamart`/`zepto` items are
    eligible only if `category` (lowercased) is on
    `READY_TO_EAT_CATEGORIES`; `None` or an unrecognized category defaults
    to exclusion, matching FR-6.1's "unless clearly ready-to-eat".
    """
    if platform == "swiggy_food":
        return True
    if category is None:
        return False
    return category.lower() in READY_TO_EAT_CATEGORIES


def _default_chat_model() -> BaseChatModel:
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if settings.llm_provider == "groq":
        kwargs["api_key"] = settings.groq_api_key
    return init_chat_model(model=settings.llm_model, model_provider=settings.llm_provider, **kwargs)


def _build_prompt(item_name: str, reference_dataset: str) -> str:
    return (
        "You estimate calories for food items ordered in India. Use only "
        "the Indian nutrition reference dataset below as grounding — map "
        "the item to its closest reference entry/entries and base your "
        "estimate on those kcal values, not on general knowledge.\n\n"
        f"Reference dataset (JSON):\n{reference_dataset}\n\n"
        f'Item to estimate: "{item_name}"\n\n'
        "Respond with only the estimated calorie count for one typical "
        "serving, as a single integer. No words, no units, no explanation."
    )


def _parse_kcal(text: str) -> int | None:
    """Parses `text` as a bare kcal integer, discarding anything else.

    The response must be *only* the number (ADR-0008 §1's "grounded, not
    guessed") — a hedge like "roughly 450 kcal" is rejected rather than
    having its first number extracted, since the model isn't guaranteed to
    put the intended answer first.
    """
    match = _KCAL_ONLY_RE.fullmatch(text.strip())
    if match is None:
        return None
    try:
        value = int(float(match.group()))
    except ValueError:
        return None
    if value <= 0 or value > MAX_PLAUSIBLE_KCAL:
        return None
    return value


def estimate_calories(item_name: str, *, chat_model: BaseChatModel | None = None) -> int | None:
    """Estimates calories for `item_name`, grounded against
    `data/indian_nutrition_reference.json` (ADR-0008 §1).

    Returns `None` — never a guess — when the model's response is
    unparseable or falls outside the plausible range `(0, MAX_PLAUSIBLE_KCAL]`.
    Takes just an item name; the caller decides eligibility beforehand via
    `is_calorie_eligible`.
    """
    model = chat_model if chat_model is not None else _default_chat_model()
    reference_dataset = _REFERENCE_DATASET_PATH.read_text(encoding="utf-8")
    prompt = _build_prompt(item_name, reference_dataset)
    response = model.invoke([HumanMessage(content=prompt)])
    content = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_kcal(content)
