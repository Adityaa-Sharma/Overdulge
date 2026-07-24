from types import SimpleNamespace

import pytest

from app.llm import calories


class FakeChatModel:
    """Minimal double for `BaseChatModel`: plain `.invoke(...)` drives
    `estimate_calories`, mirroring the fake used for `llm/budget_suggestions.py`.
    """

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.invoke_count = 0

    def invoke(self, messages: list) -> SimpleNamespace:
        index = min(self.invoke_count, len(self.replies) - 1)
        self.invoke_count += 1
        return SimpleNamespace(content=self.replies[index])


@pytest.mark.parametrize("category", [None, "snacks", "grocery", "beverages"])
def test_swiggy_food_is_always_eligible_regardless_of_category(category):
    assert calories.is_calorie_eligible("swiggy_food", category) is True


@pytest.mark.parametrize("platform", ["swiggy_instamart", "zepto"])
@pytest.mark.parametrize(
    "category", ["snacks", "beverages", "bakery", "ice-cream", "frozen desserts", "ready-to-eat"]
)
def test_instamart_and_zepto_are_eligible_for_allow_listed_categories(platform, category):
    assert calories.is_calorie_eligible(platform, category) is True


@pytest.mark.parametrize("platform", ["swiggy_instamart", "zepto"])
def test_instamart_and_zepto_allow_list_is_case_insensitive(platform):
    assert calories.is_calorie_eligible(platform, "SNACKS") is True


@pytest.mark.parametrize("platform", ["swiggy_instamart", "zepto"])
def test_instamart_and_zepto_exclude_none_category_by_default(platform):
    assert calories.is_calorie_eligible(platform, None) is False


@pytest.mark.parametrize("platform", ["swiggy_instamart", "zepto"])
def test_instamart_and_zepto_exclude_unrecognized_category(platform):
    assert calories.is_calorie_eligible(platform, "detergent") is False


def test_estimate_calories_returns_int_for_valid_grounded_response():
    model = FakeChatModel(["350"])
    assert calories.estimate_calories("Masala dosa", chat_model=model) == 350


def test_estimate_calories_extracts_integer_from_surrounding_text():
    model = FakeChatModel(["About 450 kcal for that dish."])
    assert calories.estimate_calories("Butter chicken", chat_model=model) == 450


def test_estimate_calories_returns_none_for_unparseable_response():
    model = FakeChatModel(["I'm not sure about that one."])
    assert calories.estimate_calories("Mystery item", chat_model=model) is None


def test_estimate_calories_returns_none_for_out_of_range_response():
    model = FakeChatModel([str(calories.MAX_PLAUSIBLE_KCAL + 1)])
    assert calories.estimate_calories("Absurd item", chat_model=model) is None


def test_estimate_calories_returns_none_for_zero_response():
    model = FakeChatModel(["0"])
    assert calories.estimate_calories("Water", chat_model=model) is None


def test_estimate_calories_returns_none_for_negative_response():
    model = FakeChatModel(["-50"])
    assert calories.estimate_calories("Negative item", chat_model=model) is None


def test_estimate_calories_never_raises_on_garbage_response():
    model = FakeChatModel([""])
    assert calories.estimate_calories("Empty reply item", chat_model=model) is None
