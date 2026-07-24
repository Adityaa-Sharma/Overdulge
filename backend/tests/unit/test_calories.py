from types import SimpleNamespace

import pytest

from app.llm import calories


class FakeChatModel:
    """Minimal double for `BaseChatModel`: plain `.invoke(...)` mirrors the
    convention used by `test_budget_suggestions.py`'s `FakeChatModel`.
    """

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.invoke_count = 0

    def invoke(self, messages: list) -> SimpleNamespace:
        index = min(self.invoke_count, len(self.responses) - 1)
        self.invoke_count += 1
        return SimpleNamespace(content=self.responses[index])


@pytest.mark.parametrize(
    ("platform", "category", "expected"),
    [
        ("swiggy_food", None, True),
        ("swiggy_food", "anything", True),
        ("swiggy_food", "not-ready-to-eat", True),
        ("swiggy_instamart", "snacks", True),
        ("swiggy_instamart", "Beverages", True),
        ("swiggy_instamart", "dairy", False),
        ("swiggy_instamart", None, False),
        ("zepto", "ready-to-eat", True),
        ("zepto", "ICE-CREAM", True),
        ("zepto", "vegetables", False),
        ("zepto", None, False),
        ("some_other_platform", "snacks", False),
        ("some_other_platform", None, False),
    ],
)
def test_is_calorie_eligible(platform, category, expected):
    assert calories.is_calorie_eligible(platform, category) is expected


def test_estimate_calories_returns_int_for_valid_grounded_response():
    model = FakeChatModel(["490"])

    result = calories.estimate_calories("Butter Chicken", chat_model=model)

    assert result == 490
    assert model.invoke_count == 1


def test_estimate_calories_parses_number_embedded_in_text():
    model = FakeChatModel(["About 350 kcal for this item."])

    result = calories.estimate_calories("Masala Dosa", chat_model=model)

    assert result == 350


def test_estimate_calories_returns_none_for_unparseable_response():
    model = FakeChatModel(["I'm not sure what that is."])

    result = calories.estimate_calories("Mystery Item", chat_model=model)

    assert result is None


def test_estimate_calories_returns_none_for_out_of_range_response():
    model = FakeChatModel(["8000"])

    result = calories.estimate_calories("Giant Feast", chat_model=model)

    assert result is None


def test_estimate_calories_returns_none_for_negative_response():
    model = FakeChatModel(["-50"])

    result = calories.estimate_calories("Weird Item", chat_model=model)

    assert result is None


def test_estimate_calories_returns_none_for_zero_response():
    model = FakeChatModel(["0"])

    result = calories.estimate_calories("Empty Item", chat_model=model)

    assert result is None


def test_estimate_calories_never_raises_on_garbage_response():
    model = FakeChatModel([""])

    result = calories.estimate_calories("Whatever", chat_model=model)

    assert result is None
