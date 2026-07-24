from types import SimpleNamespace

import pytest

from app.llm import calories


class FakeChatModel:
    """A minimal double for `BaseChatModel`: `invoke(...)` returns the next
    queued response in sequence, mirroring `FakeChatModel` in
    `test_llm_agent.py`.
    """

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.invoke_count = 0
        self.last_prompt: str | None = None

    def invoke(self, messages: list) -> SimpleNamespace:
        self.last_prompt = messages[0].content
        index = self.invoke_count
        self.invoke_count += 1
        content = self.responses[index] if index < len(self.responses) else self.responses[-1]
        return SimpleNamespace(content=content)


# --- is_calorie_eligible -----------------------------------------------


def test_swiggy_food_is_always_eligible_regardless_of_category():
    assert calories.is_calorie_eligible("swiggy_food", None) is True
    assert calories.is_calorie_eligible("swiggy_food", "produce") is True
    assert calories.is_calorie_eligible("swiggy_food", "snacks") is True


@pytest.mark.parametrize("platform", ["swiggy_instamart", "zepto"])
def test_grocery_platforms_eligible_only_for_allow_listed_category(platform):
    assert calories.is_calorie_eligible(platform, "snacks") is True
    assert calories.is_calorie_eligible(platform, "beverages") is True
    assert calories.is_calorie_eligible(platform, "Snacks") is True  # case-insensitive


@pytest.mark.parametrize("platform", ["swiggy_instamart", "zepto"])
def test_grocery_platforms_excluded_for_non_allow_listed_category(platform):
    assert calories.is_calorie_eligible(platform, "produce") is False
    assert calories.is_calorie_eligible(platform, "household") is False


@pytest.mark.parametrize("platform", ["swiggy_instamart", "zepto"])
def test_grocery_platforms_excluded_by_default_when_category_is_none(platform):
    assert calories.is_calorie_eligible(platform, None) is False


def test_unrecognized_platform_with_no_category_is_excluded():
    assert calories.is_calorie_eligible("some_future_platform", None) is False


# --- estimate_calories ---------------------------------------------------


def test_estimate_calories_returns_parsed_int_from_grounded_response():
    model = FakeChatModel(["450"])

    result = calories.estimate_calories("butter chicken", chat_model=model)

    assert result == 450
    assert model.invoke_count == 1


def test_estimate_calories_prompt_includes_reference_dataset_and_item_name():
    model = FakeChatModel(["300"])

    calories.estimate_calories("paneer tikka", chat_model=model)

    assert "paneer tikka" in model.last_prompt
    assert "butter chicken" in model.last_prompt  # a known reference entry


def test_estimate_calories_returns_none_for_unparseable_response():
    model = FakeChatModel(["I'm not sure, maybe a lot of calories?"])

    assert calories.estimate_calories("mystery item", chat_model=model) is None


def test_estimate_calories_returns_none_for_zero_or_negative_response():
    assert calories.estimate_calories("water", chat_model=FakeChatModel(["0"])) is None
    assert calories.estimate_calories("water", chat_model=FakeChatModel(["-50"])) is None


def test_estimate_calories_returns_none_for_response_above_plausible_ceiling():
    model = FakeChatModel([str(calories.MAX_PLAUSIBLE_KCAL + 1)])

    assert calories.estimate_calories("giant thali", chat_model=model) is None


def test_estimate_calories_accepts_response_at_the_plausible_ceiling():
    model = FakeChatModel([str(calories.MAX_PLAUSIBLE_KCAL)])
    result = calories.estimate_calories("giant thali", chat_model=model)

    assert result == calories.MAX_PLAUSIBLE_KCAL


def test_estimate_calories_never_raises_on_malformed_response():
    model = FakeChatModel([""])

    assert calories.estimate_calories("empty response item", chat_model=model) is None


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.core.config import get_settings

    yield
    get_settings.cache_clear()
