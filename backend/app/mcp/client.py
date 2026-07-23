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

import json
from typing import Any

import httpx

# The Streamable HTTP transport requires the client to accept *both* content
# types: the server replies with a single `application/json` object or a
# `text/event-stream` (SSE) stream, at its discretion. Omitting either is a hard
# 406 ("Client must accept both application/json and text/event-stream") before
# the request is even processed.
_ACCEPT = "application/json, text/event-stream"


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
                    "Accept": _ACCEPT,
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

    body = _parse_jsonrpc(response, tool_name)

    if "error" in body:
        error = body["error"] or {}
        raise McpRpcError(code=error.get("code", -1), message=error.get("message", "unknown error"))

    result = body.get("result")
    if not isinstance(result, dict):
        raise McpTransportError(f"MCP response for {tool_name!r} had no `result` object")

    return _unwrap_tool_result(result, tool_name)


def _unwrap_tool_result(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Return the tool's own output, not the MCP `tools/call` envelope.

    A spec `tools/call` result wraps the tool output in
    `{content: [...], structuredContent?: {...}, isError?: bool}` — the actual
    payload is in `structuredContent`, or JSON-encoded in a `text` content
    block. Adapters want that payload (`{"orders": [...]}`), so unwrap it here,
    where the knowledge that this is an MCP envelope belongs. A result that is
    already the bare payload (no envelope) is passed through unchanged.
    """
    if result.get("isError") is True:
        raise McpRpcError(code=-32000, message=_text_block(result) or "tool call reported an error")

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    if isinstance(result.get("content"), list):
        text = _text_block(result)
        if text is not None:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                raise McpTransportError(
                    f"MCP tool {tool_name!r} returned non-JSON text content"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
            raise McpTransportError(f"MCP tool {tool_name!r} content was not a JSON object")

    return result


def _text_block(result: dict[str, Any]) -> str | None:
    """Concatenated text of every `text` content block, or None if there are none."""
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return None
    texts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "".join(texts) if texts else None


def _parse_jsonrpc(response: httpx.Response, tool_name: str) -> dict[str, Any]:
    """Return the JSON-RPC response object, from either transport encoding.

    A Streamable HTTP server may answer a single request as one JSON object or
    as an SSE stream carrying the same object in a `data:` frame. We accept
    both and hand back the JSON-RPC envelope either way.
    """
    content_type = response.headers.get("content-type", "")
    body = (
        _parse_sse(response.text) if "text/event-stream" in content_type else _parse_json(response)
    )
    if not isinstance(body, dict):
        raise McpTransportError(f"MCP response for {tool_name!r} was not a JSON object")
    return body


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise McpTransportError("MCP response was not valid JSON") from exc


def _parse_sse(text: str) -> dict[str, Any]:
    """Pull the JSON-RPC message out of an SSE body.

    SSE frames are separated by a blank line; a frame's payload is its `data:`
    lines joined by newline. We return the last frame that parses to a JSON-RPC
    object (a stream can carry progress notifications before the real result).
    """
    message: dict[str, Any] | None = None
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip(" ")
            for line in frame.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            parsed = json.loads("\n".join(data_lines))
        except ValueError:
            continue
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            message = parsed
    if message is None:
        raise McpTransportError("MCP SSE response carried no JSON-RPC result")
    return message
