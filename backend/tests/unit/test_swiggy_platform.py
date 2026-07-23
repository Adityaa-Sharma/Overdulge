import httpx
import pytest

from app.oauth import engine
from app.oauth.platforms import swiggy

# Swiggy's real behaviour, confirmed against the live server: the RFC 8414
# §3.1 path-inserted URL answers 401, and the document is served from the host
# root instead. Discovery gave up on the first candidate, so every attempt to
# link Swiggy raised OAuthError and surfaced as a 500.
_SPEC_URL = "/.well-known/oauth-authorization-server/auth"
_ROOT_URL = "/.well-known/oauth-authorization-server"

_METADATA = {
    "issuer": "https://mcp.swiggy.com/auth",
    "authorization_endpoint": "https://mcp.swiggy.com/auth/authorize",
    "token_endpoint": "https://mcp.swiggy.com/auth/token",
    "registration_endpoint": "https://mcp.swiggy.com/auth/register",
}


def test_swiggy_config_targets_the_documented_auth_server() -> None:
    assert swiggy.CONFIG.name == "swiggy"
    assert swiggy.CONFIG.issuer == "https://mcp.swiggy.com/auth"
    assert swiggy.CONFIG.mcp_base_url == "https://mcp.swiggy.com"


def test_discovery_succeeds_against_swiggys_actual_well_known_layout() -> None:
    """Regression test for the production linking outage."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _SPEC_URL:
            return httpx.Response(401, json={"error": "invalid_token"})
        if request.url.path == _ROOT_URL:
            return httpx.Response(200, json=_METADATA)
        return httpx.Response(404, text="not found")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        metadata = engine._fetch_metadata(http, swiggy.CONFIG)

    assert metadata["registration_endpoint"] == "https://mcp.swiggy.com/auth/register"


def test_swiggy_discovery_still_tries_the_spec_url_first() -> None:
    """If Swiggy ever starts serving the spec-mandated URL, we must use it —
    the root fallback is a concession to reality, not the preferred path.
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json=_METADATA)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        engine._fetch_metadata(http, swiggy.CONFIG)

    assert requested == [_SPEC_URL]


def test_swiggy_discovery_rejects_a_document_issued_by_something_else() -> None:
    """`mcp.swiggy.com` serves the MCP endpoints as well as the auth server, so
    the host root is shared. The issuer check is what stops the fallback from
    binding us to a neighbouring service's metadata.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _ROOT_URL:
            return httpx.Response(200, json={**_METADATA, "issuer": "https://mcp.swiggy.com"})
        return httpx.Response(404, text="not found")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(engine.OAuthError):
            engine._fetch_metadata(http, swiggy.CONFIG)
