from types import SimpleNamespace

import pytest

from app.llm import agent, tools


class _BoundFakeModel:
    """Emulates the runnable `chat_model.bind_tools(...)` returns."""

    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    def invoke(self, messages: list) -> SimpleNamespace:
        self._parent.bound_invoke_count += 1
        index = self._parent.bound_invoke_count - 1
        tool_calls = (
            self._parent.tool_call_batches[index]
            if index < len(self._parent.tool_call_batches)
            else []
        )
        return SimpleNamespace(content="", tool_calls=tool_calls)


class FakeChatModel:
    """A minimal double for `BaseChatModel`: `bind_tools(...).invoke(...)`
    drives the tool-calling loop (one `tool_call_batches` entry per model
    turn); plain `.invoke(...)` drives the final fait-accompli sentence.
    """

    def __init__(self, tool_call_batches: list[list[dict]], sentences: list[str] | None = None):
        self.tool_call_batches = tool_call_batches
        self.sentences = list(sentences or ["A grounded sentence."])
        self.bound_invoke_count = 0
        self.plain_invoke_count = 0

    def bind_tools(self, tool_list: list) -> _BoundFakeModel:
        return _BoundFakeModel(self)

    def invoke(self, messages: list) -> SimpleNamespace:
        self.plain_invoke_count += 1
        index = self.plain_invoke_count - 1
        sentence = self.sentences[index] if index < len(self.sentences) else self.sentences[-1]
        return SimpleNamespace(content=sentence)


def _tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _fake_spend_total(total_paise: int, order_count: int):
    def fake(
        jwt,
        start_date,
        end_date,
        platform=None,
        category=None,
        item_name_contains=None,
        *,
        transport=None,
    ):
        return {"total_paise": total_paise, "order_count": order_count}

    return fake


COVERING_COVERAGE = {
    "earliest_order_at": "2026-01-01T00:00:00Z",
    "latest_order_at": "2026-06-30T00:00:00Z",
    "platforms_synced": ["zepto", "swiggy_food"],
}


def test_tool_schemas_never_expose_jwt(monkeypatch):
    tool_list = agent._make_tools("secret-jwt")
    for tool in tool_list:
        schema = tool.args_schema.model_json_schema()
        assert "jwt" not in schema.get("properties", {})


def test_tool_loop_caps_at_three_calls_and_grounds_on_the_last_result(monkeypatch):
    call_log: list[dict] = []

    def fake_spend_total(
        jwt,
        start_date,
        end_date,
        platform=None,
        category=None,
        item_name_contains=None,
        *,
        transport=None,
    ):
        call_log.append({"start_date": start_date, "end_date": end_date})
        return {"total_paise": len(call_log) * 10000, "order_count": len(call_log)}

    monkeypatch.setattr(tools, "spend_total", fake_spend_total)
    monkeypatch.setattr(tools, "data_coverage", lambda jwt, **kw: COVERING_COVERAGE)

    # The model keeps requesting spend_total five times; only 3 may execute.
    batches = [
        [_tool_call("spend_total", {"start_date": "2026-05-01", "end_date": "2026-05-31"}, str(i))]
        for i in range(5)
    ]
    model = FakeChatModel(batches)

    result = agent.answer_query("jwt", "how much did I spend in May?", chat_model=model)

    assert len(call_log) == 3
    assert model.bound_invoke_count == 3
    assert result["amount_paise"] == 30000
    assert result["has_data"] is True


def test_amount_paise_is_raw_tool_output_never_parsed_from_model_text(monkeypatch):
    monkeypatch.setattr(
        tools,
        "spend_total",
        _fake_spend_total(27300, 3),
    )
    monkeypatch.setattr(tools, "data_coverage", lambda jwt, **kw: COVERING_COVERAGE)

    batches = [
        [_tool_call("spend_total", {"start_date": "2026-05-01", "end_date": "2026-05-31"}, "1")]
    ]
    # Both attempts state a number that diverges from the real ₹273.00 figure.
    model = FakeChatModel(batches, sentences=["You spent ₹999.00.", "Still ₹999.00, sorry."])

    result = agent.answer_query("jwt", "how much did I spend on milk?", chat_model=model)

    assert result["amount_paise"] == 27300
    assert model.plain_invoke_count == 2
    assert "999" not in result["explanation"]
    assert result["has_data"] is True


def test_grounded_sentence_is_accepted_without_regeneration(monkeypatch):
    monkeypatch.setattr(
        tools,
        "spend_total",
        _fake_spend_total(27300, 3),
    )
    monkeypatch.setattr(tools, "data_coverage", lambda jwt, **kw: COVERING_COVERAGE)

    batches = [
        [_tool_call("spend_total", {"start_date": "2026-05-01", "end_date": "2026-05-31"}, "1")]
    ]
    model = FakeChatModel(batches, sentences=["You spent ₹273.00 across 3 orders on milk."])

    result = agent.answer_query("jwt", "how much did I spend on milk?", chat_model=model)

    assert result["amount_paise"] == 27300
    assert model.plain_invoke_count == 1
    assert result["explanation"] == "You spent ₹273.00 across 3 orders on milk."


def test_not_enough_data_when_user_has_no_linked_accounts(monkeypatch):
    monkeypatch.setattr(
        tools,
        "data_coverage",
        lambda jwt, **kw: {
            "earliest_order_at": None,
            "latest_order_at": None,
            "platforms_synced": [],
        },
    )
    spend_total_called = []
    monkeypatch.setattr(
        tools,
        "spend_total",
        lambda *a, **kw: spend_total_called.append(1) or {"total_paise": 0, "order_count": 0},
    )
    model = FakeChatModel([[]])

    result = agent.answer_query("jwt", "how much did I spend?", chat_model=model)

    assert result == {
        "amount_paise": None,
        "explanation": agent.NOT_ENOUGH_DATA_EXPLANATION,
        "has_data": False,
    }
    assert model.bound_invoke_count == 0
    assert not spend_total_called


def test_not_enough_data_when_question_range_has_no_coverage_overlap(monkeypatch):
    monkeypatch.setattr(
        tools,
        "data_coverage",
        lambda jwt, **kw: {
            "earliest_order_at": "2026-06-01T00:00:00Z",
            "latest_order_at": "2026-06-30T00:00:00Z",
            "platforms_synced": ["zepto"],
        },
    )
    monkeypatch.setattr(
        tools,
        "spend_total",
        _fake_spend_total(0, 0),
    )
    batches = [
        [_tool_call("spend_total", {"start_date": "2026-01-01", "end_date": "2026-01-31"}, "1")]
    ]
    model = FakeChatModel(batches)

    result = agent.answer_query("jwt", "how much did I spend in January?", chat_model=model)

    assert result["has_data"] is False
    assert result["amount_paise"] is None
    assert result["explanation"] == agent.NOT_ENOUGH_DATA_EXPLANATION


def test_genuine_zero_row_in_covered_range_is_a_real_zero_not_a_fallback(monkeypatch):
    monkeypatch.setattr(tools, "data_coverage", lambda jwt, **kw: COVERING_COVERAGE)
    monkeypatch.setattr(
        tools,
        "spend_total",
        _fake_spend_total(0, 0),
    )
    batches = [
        [_tool_call("spend_total", {"start_date": "2026-05-01", "end_date": "2026-05-31"}, "1")]
    ]
    model = FakeChatModel(batches, sentences=["You spent ₹0.00 across 0 orders."])

    result = agent.answer_query("jwt", "how much did I spend on caviar in May?", chat_model=model)

    assert result["has_data"] is True
    assert result["amount_paise"] == 0
    assert result["explanation"] != agent.NOT_ENOUGH_DATA_EXPLANATION


def test_model_never_calling_a_tool_is_not_enough_data(monkeypatch):
    monkeypatch.setattr(tools, "data_coverage", lambda jwt, **kw: COVERING_COVERAGE)
    model = FakeChatModel([[]])  # first turn already has no tool calls

    result = agent.answer_query("jwt", "what's the weather like?", chat_model=model)

    assert result["has_data"] is False
    assert result["amount_paise"] is None


# --- reusable numeric-grounding guard (ADR-0003, reused by ADR-0008 §5) -----


def test_numbers_are_grounded_accepts_exact_and_bare_integer_form():
    assert agent.numbers_are_grounded("Total: ₹1,234.00 across 3 orders.", ["₹1,234.00", 3])
    assert agent.numbers_are_grounded("Total: 1234 across 3 orders.", ["₹1,234.00", 3])


def test_numbers_are_grounded_rejects_a_diverging_number():
    assert not agent.numbers_are_grounded("Total: ₹999.00.", ["₹1,234.00"])


def test_format_inr_uses_indian_digit_grouping():
    assert agent.format_inr(123456) == "₹1,234.56"
    assert agent.format_inr(1234567800) == "₹1,23,45,678.00"
    assert agent.format_inr(27300) == "₹273.00"


def test_generate_grounded_sentence_falls_back_after_two_diverging_attempts():
    model = FakeChatModel([[]], sentences=["₹1.00 is wrong", "₹2.00 also wrong"])

    sentence = agent.generate_grounded_sentence(
        model, "prompt", ["₹273.00"], fallback="Fallback sentence."
    )

    assert sentence == "Fallback sentence."
    assert model.plain_invoke_count == 2


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.core.config import get_settings

    yield
    get_settings.cache_clear()
