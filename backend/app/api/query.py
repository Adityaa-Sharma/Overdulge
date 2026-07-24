"""Query API route (FR-4, #37; ADR-0002/ADR-0003): `POST /api/v1/query`
runs the caller's free-text spending question through `llm/agent.py`'s
capped tool-calling loop, forwarding the caller's own Supabase JWT
(user-JWT-forwarding mode only, never service-role, per ADR-0002) — the
agent's tools are RLS-scoped by that JWT, so this route never adds a
user filter of its own.

`answer_query` is synchronous (LangChain + PostgREST calls), so it runs in
the default executor under a hard timeout matching the parent issue's
documented <10s interactive-latency target (issue #5 AC-5). A timeout or
any agent-side failure returns SYSTEM.md §4's `{"error": {"code",
"message"}}` envelope rather than a bare 500 with a stack-trace body.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.auth import AuthedUser, get_current_user
from app.core.safe_log import log_event
from app.llm import agent

router = APIRouter()

# Issue #5 AC-5's documented interactive-latency target.
_QUERY_TIMEOUT_SECONDS = 10.0

_TIMEOUT_MESSAGE = "That question is taking too long to answer — please try again."
_AGENT_ERROR_MESSAGE = "Something went wrong answering that question — please try again."


class QueryIn(BaseModel):
    question: str


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


@router.post("/query", response_model=None)
async def submit_query(
    body: QueryIn, user: AuthedUser = Depends(get_current_user)
) -> dict[str, Any] | JSONResponse:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, agent.answer_query, user.jwt, body.question),
            timeout=_QUERY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log_event("error", "query agent timed out", user_id=user.user_id, route="POST /query")
        return _error_response(504, "timeout", _TIMEOUT_MESSAGE)
    except Exception as exc:
        log_event(
            "error",
            "query agent failed",
            user_id=user.user_id,
            route="POST /query",
            error_type=type(exc).__name__,
        )
        return _error_response(502, "agent_error", _AGENT_ERROR_MESSAGE)
