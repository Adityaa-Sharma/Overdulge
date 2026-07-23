import pytest

from app.llm import tone_guard


def test_check_tone_passes_playful_ordering_and_money_sentence():
    text = "Three orders this week — your wallet is doing the heavy lifting, not you."
    assert tone_guard.check_tone(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Time to lose weight before your next order.",
        "That's a lot of calories burned just thinking about it.",
        "Maybe it's time to start a diet.",
        "Your BMI might thank you for skipping this one.",
        "You've been overweight lately, have you noticed?",
        "You should eat less next time.",
    ],
)
def test_check_tone_fails_body_weight_and_dieting_language(text):
    assert tone_guard.check_tone(text) is False


def test_fallback_blurb_passes_check_tone():
    assert tone_guard.check_tone(tone_guard.FALLBACK_BLURB) is True
