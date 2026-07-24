"""LangChain NL query agent — capped tool-calling loop, numeric grounding,
"not enough data" handling (FR-4; ADR-0003:
docs/architecture/decisions/0003-nl-query-engine-tool-calling.md).

Binds `llm/tools.py`'s read-only functions as LangChain tools over a chat
model from `init_chat_model` and runs a tool-calling loop capped at
`MAX_TOOL_CALLS` calls per question (latency target + a hard ceiling per
ADR-0003). The model never re-types a number: `answer_query`'s
`amount_paise` is always the raw value from a tool's structured result, and
the model is only ever asked to write the explanatory sentence *around* a
number it is handed as a fait accompli. `generate_grounded_sentence` is the
reusable post-generation guard other FR-4/FR-5/FR-6 callers import
unmodified (ADR-0003, ADR-0008 §5) rather than reimplementing.
"""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.core.config import get_settings
from app.llm import tools

MAX_TOOL_CALLS = 3

_ANSWERABLE_TOOL_NAMES = frozenset(
    {"spend_total", "spend_by_category", "spend_by_platform", "top_items"}
)

_SYSTEM_PROMPT = (
    "You are Overdulge's spend query assistant. Answer the user's question "
    "about their own order history by calling the provided tools — never "
    "answer from memory or invent a figure. Call `data_coverage` first if "
    "you are unsure whether the requested range/platform has synced data. "
    "If no combination of the available tools can answer the question, do "
    "not call any more tools and say so plainly."
)

NOT_ENOUGH_DATA_EXPLANATION = (
    "I don't have enough data to answer that — link an account or ask about "
    "a range with synced orders."
)

_NUMERIC_TOKEN_RE = re.compile(r"₹?-?\d[\d,]*(?:\.\d+)?")


class ToolInvocation(NamedTuple):
    name: str
    args: dict[str, Any]
    result: Any


def _default_chat_model() -> BaseChatModel:
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if settings.llm_provider == "groq":
        kwargs["api_key"] = settings.groq_api_key
    return init_chat_model(model=settings.llm_model, model_provider=settings.llm_provider, **kwargs)


def _make_tools(jwt: str, *, transport: Any | None = None) -> list[StructuredTool]:
    def spend_total(
        start_date: str,
        end_date: str,
        platform: str | None = None,
        category: str | None = None,
        item_name_contains: str | None = None,
    ) -> dict[str, int]:
        return tools.spend_total(
            jwt,
            start_date,
            end_date,
            platform,
            category,
            item_name_contains,
            transport=transport,
        )

    def spend_by_category(start_date: str, end_date: str) -> list[dict[str, Any]]:
        return tools.spend_by_category(jwt, start_date, end_date, transport=transport)

    def spend_by_platform(start_date: str, end_date: str) -> list[dict[str, Any]]:
        return tools.spend_by_platform(jwt, start_date, end_date, transport=transport)

    def top_items(start_date: str, end_date: str, limit: int = 10) -> list[dict[str, Any]]:
        return tools.top_items(jwt, start_date, end_date, limit, transport=transport)

    def data_coverage() -> dict[str, Any]:
        return tools.data_coverage(jwt, transport=transport)

    return [
        StructuredTool.from_function(
            spend_total,
            name="spend_total",
            description=(
                "Total spend (paise) and order count in a date range "
                "[start_date, end_date] (YYYY-MM-DD, inclusive), optionally "
                "filtered by platform, item category, or item name substring."
            ),
        ),
        StructuredTool.from_function(
            spend_by_category,
            name="spend_by_category",
            description="Spend broken down by item category in a date range, highest first.",
        ),
        StructuredTool.from_function(
            spend_by_platform,
            name="spend_by_platform",
            description="Spend broken down by platform in a date range, highest first.",
        ),
        StructuredTool.from_function(
            top_items,
            name="top_items",
            description="The highest-spend item names in a date range.",
        ),
        StructuredTool.from_function(
            data_coverage,
            name="data_coverage",
            description=(
                "The synced order-history window: earliest/latest order date "
                "and platforms with synced data. Use this to check whether a "
                "range/platform actually has data before trusting a zero result."
            ),
        ),
    ]


def _date_range_covered(args: dict[str, Any], coverage: dict[str, Any]) -> bool:
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    if start_date is None or end_date is None:
        return True
    earliest = coverage.get("earliest_order_at")
    latest = coverage.get("latest_order_at")
    if earliest is None or latest is None:
        return False
    if start_date > latest[:10] or end_date < earliest[:10]:
        return False
    platform = args.get("platform")
    if platform is not None and platform not in coverage.get("platforms_synced", []):
        return False
    return True


def _group_indian_digits(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    last_three = digits[-3:]
    remainder = digits[:-3]
    groups: list[str] = []
    while len(remainder) > 2:
        groups.insert(0, remainder[-2:])
        remainder = remainder[:-2]
    if remainder:
        groups.insert(0, remainder)
    return ",".join([*groups, last_three])


def format_inr(paise: int) -> str:
    """Formats integer paise as an INR display string with Indian digit
    grouping, e.g. `format_inr(123456)` -> "₹1,234.56" (BRD §2.5, matching
    the frontend's `formatPaise`).
    """
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    return f"₹{sign}{_group_indian_digits(str(whole))}.{frac:02d}"


def _normalize_numeric_token(token: str) -> str:
    return token.lstrip("₹").replace(",", "")


def find_numeric_tokens(text: str) -> list[str]:
    return [_normalize_numeric_token(match.group()) for match in _NUMERIC_TOKEN_RE.finditer(text)]


def _expand_allowed_tokens(value: object) -> set[str]:
    normalized = _normalize_numeric_token(str(value))
    expanded = {normalized}
    integer_part, dot, frac = normalized.partition(".")
    if dot and set(frac) == {"0"}:
        expanded.add(integer_part)
    return expanded


def numbers_are_grounded(sentence: str, allowed_values: list[object]) -> bool:
    """True when every ₹-prefixed or bare numeric token in `sentence` matches
    one of `allowed_values` (the code-computed figures the model was handed).
    """
    allowed: set[str] = set()
    for value in allowed_values:
        allowed |= _expand_allowed_tokens(value)
    return all(token in allowed for token in find_numeric_tokens(sentence))


def generate_grounded_sentence(
    model: BaseChatModel,
    prompt: str,
    allowed_values: list[object],
    *,
    fallback: str,
) -> str:
    """Fait-accompli generation with a post-generation numeric-grounding
    guard (ADR-0003): asks `model` for one sentence built around figures
    already computed in code, regenerates once if the model's sentence
    states a number outside `allowed_values`, and falls back to a templated
    sentence if it still diverges. Reused unmodified by FR-5/FR-6 callers
    (ADR-0008 §5) — do not fork this per caller.
    """
    for _ in range(2):
        response = model.invoke([HumanMessage(content=prompt)])
        sentence = response.content if isinstance(response.content, str) else str(response.content)
        if numbers_are_grounded(sentence, allowed_values):
            return sentence
    return fallback


def _fait_accompli_prompt(question: str, fact_summary: str) -> str:
    return (
        f'The user asked: "{question}"\n'
        f"You computed: {fact_summary}.\n"
        "Write exactly one short, natural sentence explaining this to the "
        "user. Use only the figures above — do not compute, restate, or "
        "guess any other number."
    )


def _summarize_invocation(invocation: ToolInvocation) -> tuple[int | None, list[object], str]:
    if invocation.name == "spend_total":
        total_paise = invocation.result["total_paise"]
        order_count = invocation.result["order_count"]
        formatted = format_inr(total_paise)
        noun = "order" if order_count == 1 else "orders"
        summary = f"total spend = {formatted} across {order_count} {noun}"
        return total_paise, [formatted, order_count], summary

    label_field = {"spend_by_category": "category", "spend_by_platform": "platform"}.get(
        invocation.name, "name"
    )
    rows: list[dict[str, Any]] = invocation.result
    if not rows:
        return None, [], "no matching spend in that range"

    allowed: list[object] = []
    parts: list[str] = []
    for row in rows[:5]:
        formatted = format_inr(row["total_paise"])
        allowed.append(formatted)
        parts.append(f"{row[label_field]} = {formatted}")
    return None, allowed, "; ".join(parts)


def answer_query(
    jwt: str,
    question: str,
    *,
    chat_model: BaseChatModel | None = None,
    transport: Any | None = None,
) -> dict[str, Any]:
    """Answers a natural-language spend question, grounded in tool output.

    Returns `{"amount_paise": int | None, "explanation": str, "has_data":
    bool}`. `amount_paise` is always the raw output of a tool call (never
    parsed from the model's text) and is `None` for answers that break down
    spend across multiple rows rather than stating a single total.
    """
    model = chat_model if chat_model is not None else _default_chat_model()
    tool_list = _make_tools(jwt, transport=transport)
    coverage = next(tool.func() for tool in tool_list if tool.name == "data_coverage")

    if not coverage.get("platforms_synced"):
        return {"amount_paise": None, "explanation": NOT_ENOUGH_DATA_EXPLANATION, "has_data": False}

    invocation = _run_tool_loop(model, question, tool_list)

    if invocation is None or not _date_range_covered(invocation.args, coverage):
        return {"amount_paise": None, "explanation": NOT_ENOUGH_DATA_EXPLANATION, "has_data": False}

    amount_paise, allowed_values, fact_summary = _summarize_invocation(invocation)
    prompt = _fait_accompli_prompt(question, fact_summary)
    fallback = (
        f"{fact_summary[0].upper()}{fact_summary[1:]}."
        if fact_summary
        else NOT_ENOUGH_DATA_EXPLANATION
    )
    explanation = generate_grounded_sentence(model, prompt, allowed_values, fallback=fallback)
    return {"amount_paise": amount_paise, "explanation": explanation, "has_data": True}


def _run_tool_loop(
    model: BaseChatModel, question: str, tool_list: list[StructuredTool]
) -> ToolInvocation | None:
    tools_by_name = {tool.name: tool for tool in tool_list}
    model_with_tools = model.bind_tools(tool_list)
    messages: list[Any] = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=question)]

    last_answer: ToolInvocation | None = None
    calls_made = 0

    while calls_made < MAX_TOOL_CALLS:
        ai_message = model_with_tools.invoke(messages)
        messages.append(ai_message)
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            break
        for tool_call in tool_calls:
            if calls_made >= MAX_TOOL_CALLS:
                break
            tool = tools_by_name.get(tool_call["name"])
            if tool is None:
                continue
            result = tool.func(**tool_call["args"])
            calls_made += 1
            if tool_call["name"] in _ANSWERABLE_TOOL_NAMES:
                last_answer = ToolInvocation(tool_call["name"], tool_call["args"], result)
            messages.append(ToolMessage(content=json.dumps(result), tool_call_id=tool_call["id"]))

    return last_answer
