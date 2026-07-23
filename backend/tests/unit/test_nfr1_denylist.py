from __future__ import annotations

import pytest

from app.core.nfr1_denylist import MUTATING_TOOL_DENYLIST
from scripts.check_nfr1_denylist import build_denylist_pattern

_pattern = build_denylist_pattern()


@pytest.mark.parametrize("tool_name", sorted(MUTATING_TOOL_DENYLIST))
def test_pattern_matches_each_denylisted_name(tool_name):
    fixture = f"result = mcp_client.call('{tool_name}', payload)"

    assert _pattern.search(fixture), f"pattern did not match denylisted name {tool_name!r}"


@pytest.mark.parametrize("tool_name", sorted(MUTATING_TOOL_DENYLIST))
def test_pattern_respects_word_boundaries(tool_name):
    fixture = f"svn_{tool_name}_v2"

    assert not _pattern.search(fixture), (
        f"pattern matched {tool_name!r} as a substring of an unrelated identifier"
    )


def test_denylist_values_are_non_empty_platform_tags():
    for tool_name, platform_tag in MUTATING_TOOL_DENYLIST.items():
        assert platform_tag, f"{tool_name!r} is missing a platform tag"
