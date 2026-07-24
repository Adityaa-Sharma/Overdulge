"""Query API route (FR-4 AC-1/AC-5, ADR-0003): `POST /api/v1/query`, the
authenticated entrypoint the frontend (#38) calls to ask a free-text spend
question. Forwards the caller's own Supabase JWT into `llm/agent.py`'s
`answer_query` (user-JWT-forwarding mode only, ADR-0002 — never
service-role) and returns its `{amount_paise, explanation, has_data}` shape
unmodified; numeric grounding is enforced entirely inside `answer_query`
(ADR-0003), not here.

`answer_query` is a blocking call (sync httpx + sync LangChain `invoke`), so
it runs on a worker thread under an `asyncio.wait_for` deadline — the
request-level timeout FR-4 AC-5 asks for, independent of `agent.py`'s own
`MAX_TOOL_CALLS` cap. A timeout or any other agent-layer failure returns
SYSTEM.md's `{"error": {"code", "message"}}` envelope rather than a bare
500/stack trace; "not enough data" is a normal 200 (`has_data: false`) since
it is a real, grounded answer, not a failure.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.auth import AuthedUser, get_current_user
from app.core.config import get_settings
from app.core.safe_log import log_event
from app.llm import agent

router = APIRouter()

_ROUTE = "POST /api/v1/query"
_TIMEOUT_MESSAGE = "That question is taking too long to answer — please try again."
_AGENT_ERROR_MESSAGE = "Something went wrong answering that question — please try again."


class QueryIn(BaseModel):
    question: str = Field(min_length=1)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


@router.post("/query", response_model=None)
async def ask_query(
    body: QueryIn, user: AuthedUser = Depends(get_current_user)
) -> dict[str, Any] | JSONResponse:
    settings = get_settings()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(agent.answer_query, user.jwt, body.question),
            timeout=settings.query_timeout_seconds,
        )
    except TimeoutError:
        log_event(
            "error",
            "query agent timed out",
            user_id=user.user_id,
            route=_ROUTE,
            error_type="TimeoutError",
        )
        return _error_response(504, "timeout", _TIMEOUT_MESSAGE)
    except Exception as exc:
        # Broad by design: this route's contract (issue #37) is that no
        # agent-layer failure — DB, LLM provider, or a bug in `agent.py` —
        # ever reaches the caller as an unhandled 500/stack trace. Every
        # such failure is logged with its type and translated to the
        # standard error envelope here, not swallowed.
        log_event(
            "error",
            "query agent failed",
            user_id=user.user_id,
            route=_ROUTE,
            error_type=type(exc).__name__,
        )
        return _error_response(502, "agent_error", _AGENT_ERROR_MESSAGE)

    return result
