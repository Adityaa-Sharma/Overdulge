"""Code-level tone-safety backstop for LLM-generated commentary (ADR-0008 §4:
docs/architecture/decisions/0008-calorie-estimation-grounding-and-tone-safety.md).

FR-6.3 requires calorie-related copy to stay playful about ordering habits
and money only — never about the user's body, weight, or eating, and never
prescriptive dieting language. QA's AC-4 content check is the acceptance
gate; this module is the structural backstop that runs before any such
string reaches a user, catching a bad completion between QA passes.

`_DENYLIST_PATTERNS` is a living list: extend it in place as QA's AC-4
content check surfaces misses, rather than treating it as closed.
"""

from __future__ import annotations

import re

_DENYLIST_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\blose\s+weight\b",
        r"\blosing\s+weight\b",
        r"\blost\s+weight\b",
        r"\bgain(?:ing)?\s+weight\b",
        r"\bweight\s+(?:loss|gain)\b",
        r"\bover\s*weight\b",
        r"\bunder\s*weight\b",
        r"\bobes(?:e|ity)\b",
        r"\bbmi\b",
        r"\bbody\s+mass\s+index\b",
        r"\bcalories?\s+burn(?:ed|t)?\b",
        r"\bdiet(?:ing|s)?\b",
        r"\bshould\s+eat\s+(?:less|more)\b",
        r"\beat\s+less\b",
        r"\bcut\s+(?:back|down)\s+on\s+(?:eating|food)\b",
        r"\bshed\s+(?:the\s+)?(?:pounds|kilos|kgs?)\b",
        r"\bskip(?:ping)?\s+(?:a\s+)?meals?\b",
    )
)

FALLBACK_BLURB = "Another order lands on the tab — your wallet clocked it before anyone else did."


def check_tone(text: str) -> bool:
    """True when `text` is safe to show as-is (no body/weight/dieting language)."""
    return not any(pattern.search(text) for pattern in _DENYLIST_PATTERNS)
