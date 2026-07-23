"""Generic JSON-RPC-over-streamable-HTTP client for MCP order-history tools.
Design rationale: issue #3 / #44. (The order-sync ADR proposed in #32 has not
merged yet and, once it does, cannot be ADR-0003 — that number is already
taken by docs/architecture/decisions/0003-nl-query-engine-tool-calling.md —
so it isn't cited here until the real number is assigned.)

Platform-blind by design: it has no knowledge of Swiggy, Instamart, or
Zepto, and no knowledge of which tool names are safe to call — that split
lives entirely in `mcp/adapters/*` (SYSTEM.md module-boundary rule). It does
not refresh or retry on auth failure; the caller (sync orchestration) is the
only layer that knows how to refresh a token.
"""

from __future__ import annotations

from typing import Any

import httpx


class McpError(RuntimeError):
    """Base class for all errors raised by this MCP client."""


class McpTransportError(McpError):
    """The HTTP transport failed, or the response was not a usable
    JSON-RPC envelope (non-2xx status other than 401/403, malformed/non-JSON
    body, or a body missing the expected `result` object).
    """


class McpAuthError(McpError):
    """The MCP server rejected the access token (HTTP 401/403). Never
    retried by this client — the caller refreshes the token and retries.
    """


class McpRpcError(McpError):
    """The request reached the server and got a well-formed JSON-RPC
    `error` response (unknown tool, bad params, etc.) — distinct from a
    transport/auth failure.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"MCP tool call failed ({code}): {message}")
        self.code = code
        self.message = message


def call_tool(
    base_url: str,
    access_token: str,
    tool_name: str,
    params: dict[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Sends a JSON-RPC `tools/call` request for `tool_name` and returns the
    tool's result payload.

    Raises `McpAuthError` on HTTP 401/403, `McpRpcError` on a JSON-RPC-level
    `error` field, and `McpTransportError` on any other transport/HTTP/
    response-shape failure.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": params},
    }

    with httpx.Client(transport=transport) as client:
        try:
            response = client.post(
                base_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise McpTransportError(f"MCP request to {tool_name!r} failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise McpAuthError(
            f"MCP server rejected the access token calling {tool_name!r} ({response.status_code})"
        )
    if response.status_code >= 400:
        raise McpTransportError(
            f"MCP request to {tool_name!r} failed ({response.status_code}): {response.text}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise McpTransportError(f"MCP response for {tool_name!r} was not valid JSON") from exc

    if not isinstance(body, dict):
        raise McpTransportError(f"MCP response for {tool_name!r} was not a JSON object")

    if "error" in body:
        error = body["error"] or {}
        raise McpRpcError(code=error.get("code", -1), message=error.get("message", "unknown error"))

    result = body.get("result")
    if not isinstance(result, dict):
        raise McpTransportError(f"MCP response for {tool_name!r} had no `result` object")

    return result
