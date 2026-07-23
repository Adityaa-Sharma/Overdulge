import json

import httpx
import pytest

from app.mcp import client


def _sent_json(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_call_tool_sends_json_rpc_envelope_and_returns_result():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"orders": [{"id": "abc"}]}}
        )

    result = client.call_tool(
        "https://mcp.example.com/rpc",
        "access-token",
        "get_orders",
        {"orderType": "DASH"},
        transport=_transport(handler),
    )

    assert result == {"orders": [{"id": "abc"}]}
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer access-token"
    # Streamable HTTP rejects the request with 406 unless BOTH types are
    # accepted — the exact failure that stopped every real sync.
    accept = request.headers["Accept"]
    assert "application/json" in accept
    assert "text/event-stream" in accept
    sent = _sent_json(request)
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "tools/call"
    assert sent["params"] == {"name": "get_orders", "arguments": {"orderType": "DASH"}}


def test_call_tool_parses_a_result_delivered_as_sse():
    # Streamable HTTP servers may answer with text/event-stream instead of a
    # plain JSON object; the result rides in a `data:` frame.
    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"orders":[{"id":"sse-1"}]}}\n\n'
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    result = client.call_tool(
        "https://mcp.example.com/rpc",
        "access-token",
        "get_orders",
        {},
        transport=_transport(handler),
    )

    assert result == {"orders": [{"id": "sse-1"}]}


def test_call_tool_returns_the_final_frame_when_sse_carries_progress_first():
    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"p":1}}\n\n'
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"orders":[]}}\n\n'
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    result = client.call_tool(
        "https://mcp.example.com/rpc",
        "access-token",
        "get_orders",
        {},
        transport=_transport(handler),
    )

    assert result == {"orders": []}


def test_call_tool_raises_typed_error_on_json_rpc_error_delivered_as_sse():
    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"nope"}}\n\n'
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    with pytest.raises(client.McpRpcError) as excinfo:
        client.call_tool(
            "https://mcp.example.com/rpc", "access-token", "x", {}, transport=_transport(handler)
        )
    assert excinfo.value.code == -32601


def test_call_tool_raises_typed_error_on_json_rpc_error_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            },
        )

    with pytest.raises(client.McpRpcError) as excinfo:
        client.call_tool(
            "https://mcp.example.com/rpc",
            "access-token",
            "unknown_tool",
            {},
            transport=_transport(handler),
        )

    assert excinfo.value.code == -32601
    assert excinfo.value.message == "Method not found"


@pytest.mark.parametrize("status_code", [401, 403])
def test_call_tool_raises_mcp_auth_error_on_401_or_403(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="unauthorized")

    with pytest.raises(client.McpAuthError):
        client.call_tool(
            "https://mcp.example.com/rpc",
            "expired-token",
            "get_orders",
            {},
            transport=_transport(handler),
        )


def test_call_tool_raises_transport_error_on_other_http_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(client.McpTransportError):
        client.call_tool(
            "https://mcp.example.com/rpc",
            "access-token",
            "get_orders",
            {},
            transport=_transport(handler),
        )


def test_call_tool_raises_transport_error_on_malformed_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    with pytest.raises(client.McpTransportError):
        client.call_tool(
            "https://mcp.example.com/rpc",
            "access-token",
            "get_orders",
            {},
            transport=_transport(handler),
        )


def test_call_tool_raises_transport_error_when_result_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1})

    with pytest.raises(client.McpTransportError):
        client.call_tool(
            "https://mcp.example.com/rpc",
            "access-token",
            "get_orders",
            {},
            transport=_transport(handler),
        )
