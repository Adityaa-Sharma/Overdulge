from types import SimpleNamespace

from app.llm import budget_suggestions, tools


class FakeChatModel:
    """Minimal double for `BaseChatModel`: plain `.invoke(...)` drives
    `generate_grounded_sentence` (`llm/agent.py`), reused unmodified here.
    """

    def __init__(self, sentences: list[str]):
        self.sentences = list(sentences)
        self.invoke_count = 0

    def invoke(self, messages: list) -> SimpleNamespace:
        index = min(self.invoke_count, len(self.sentences) - 1)
        self.invoke_count += 1
        return SimpleNamespace(content=self.sentences[index])


def _progress_row(**overrides):
    row = {
        "category": "food",
        "cap_paise": 10000,
        "spent_paise": 9000,
        "pct": 0.9,
        "status": "over",
    }
    row.update(overrides)
    return row


def _fake_top_items(items: list[dict], calls: list[tuple]):
    def fake(jwt, start_date, end_date, category=None, limit=5, *, transport=None):
        calls.append((jwt, start_date, end_date, category))
        return items

    return fake


def test_generates_suggestion_referencing_items_for_near_or_over_category(monkeypatch):
    items = [{"name": "Pizza", "order_count": 2, "total_paise": 5000}]
    calls: list[tuple] = []
    monkeypatch.setattr(tools, "top_items_in_category", _fake_top_items(items, calls))
    model = FakeChatModel(["Cut back on Pizza (₹50.00) to stay under your ₹100.00 cap."])

    result = budget_suggestions.generate_suggestions(
        "jwt", [_progress_row()], "2026-05-01", chat_model=model
    )

    assert result == [
        {"category": "food", "text": "Cut back on Pizza (₹50.00) to stay under your ₹100.00 cap."}
    ]
    assert calls == [("jwt", "2026-05-01", "2026-05-31", "food")]


def test_overall_cap_row_calls_top_items_in_category_with_no_category(monkeypatch):
    items = [{"name": "Pizza", "order_count": 1, "total_paise": 5000}]
    calls: list[tuple] = []
    monkeypatch.setattr(tools, "top_items_in_category", _fake_top_items(items, calls))
    model = FakeChatModel(["A grounded sentence about ₹50.00."])

    budget_suggestions.generate_suggestions(
        "jwt", [_progress_row(category=None)], "2026-05-01", chat_model=model
    )

    assert calls == [("jwt", "2026-05-01", "2026-05-31", None)]


def test_skips_near_or_over_category_with_no_matching_items(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(tools, "top_items_in_category", _fake_top_items([], calls))
    model = FakeChatModel(["should never be used"])

    result = budget_suggestions.generate_suggestions(
        "jwt", [_progress_row(category="uncategorized")], "2026-05-01", chat_model=model
    )

    assert result == []
    assert model.invoke_count == 0


def test_all_ok_progress_rows_produce_no_suggestions_without_calling_the_model(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        tools, "top_items_in_category", _fake_top_items([{"name": "x", "total_paise": 1}], calls)
    )
    model = FakeChatModel(["should never be used"])

    result = budget_suggestions.generate_suggestions(
        "jwt", [_progress_row(status="ok")], "2026-05-01", chat_model=model
    )

    assert result == []
    assert calls == []
    assert model.invoke_count == 0


def test_numeric_grounding_guard_regenerates_on_ungrounded_response(monkeypatch):
    items = [{"name": "Pizza", "order_count": 1, "total_paise": 5000}]
    monkeypatch.setattr(tools, "top_items_in_category", _fake_top_items(items, []))
    model = FakeChatModel(
        [
            "You've way overspent — nearly ₹999 over budget!",
            "Cut back on Pizza (₹50.00) to stay under your ₹100.00 cap.",
        ]
    )

    result = budget_suggestions.generate_suggestions(
        "jwt", [_progress_row()], "2026-05-01", chat_model=model
    )

    assert model.invoke_count == 2
    assert result == [
        {"category": "food", "text": "Cut back on Pizza (₹50.00) to stay under your ₹100.00 cap."}
    ]


def test_numeric_grounding_guard_falls_back_to_template_after_two_ungrounded_responses(
    monkeypatch,
):
    items = [{"name": "Pizza", "order_count": 1, "total_paise": 5000}]
    monkeypatch.setattr(tools, "top_items_in_category", _fake_top_items(items, []))
    model = FakeChatModel(["₹999 over budget!", "still ₹999 over budget!"])

    result = budget_suggestions.generate_suggestions(
        "jwt", [_progress_row()], "2026-05-01", chat_model=model
    )

    assert model.invoke_count == 2
    assert len(result) == 1
    assert "Pizza" in result[0]["text"]
    assert "999" not in result[0]["text"]
