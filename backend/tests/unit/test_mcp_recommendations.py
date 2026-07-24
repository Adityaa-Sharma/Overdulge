from app.mcp.recommendations import SearchResultItem, slugify


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Amul Butter 100g") == "amul-butter-100g"


def test_slugify_collapses_repeated_punctuation():
    assert slugify("Toned Milk, 1L (Pack of 2)") == "toned-milk-1l-pack-of-2"


def test_slugify_falls_back_to_item_for_an_all_punctuation_name():
    assert slugify("!!!") == "item"


def test_slugify_strips_leading_and_trailing_separators():
    assert slugify("  -Milk-  ") == "milk"


def test_search_result_item_carries_the_raw_payload_untouched():
    raw = {"name": "Milk", "price": 6000, "extra": "field"}

    item = SearchResultItem(
        name="Milk",
        unit_price_paise=6000,
        redirect_url="https://example.com/milk",
        raw=raw,
    )

    assert item.raw == raw
