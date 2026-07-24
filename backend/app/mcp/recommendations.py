"""Canonical shape for a live search result backing FR-7.2 (ADR-0004 §2), plus
a shared URL-slugging helper used by every adapter's `redirect_url` builder.

Platform-blind by design, like `mcp/client.py` — it carries no tool names or
URL shapes; those stay in `mcp/adapters/*` per the module-boundary rule
(SYSTEM.md §2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class SearchResultItem:
    """One live `search_products`/`search_menu` result (ADR-0004 §2) — never
    cached, fetched fresh on every FR-7.2 request. `raw` carries the
    platform's own response so the route can compare price/calorie axes
    (ADR-0004 §4) without this module needing to know those fields.
    """

    name: str
    unit_price_paise: int | None
    redirect_url: str
    raw: dict[str, Any]


def slugify(text: str) -> str:
    """A URL-safe slug for a `redirect_url` path segment. Never empty — falls
    back to `"item"` so a redirect URL is never malformed by a name that
    slugifies to nothing (e.g. all punctuation).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"
