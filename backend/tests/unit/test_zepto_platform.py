"""Zepto-specific proof that the generic OAuth engine (already exhaustively
covered against a synthetic platform in `test_oauth_engine.py`) works
end-to-end against Zepto's real config: discovery URL, DCR, token exchange,
and state validation (issue #19, AC-3).
"""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core import crypto, db
from app.core.config import Settings, get_settings
from app.oauth import engine
from app.oauth.platforms import zepto

_TEST_KEY = base64.b64encode(b"0" * 32).decode("ascii")

_METADATA = {
    "authorization_endpoint": "https://auth.zepto.co.in/authorize",
    "token_endpoint": "https://auth.zepto.co.in/token",
    "registration_endpoint": "https://auth.zepto.co.in/register",
}


def test_zepto_config_targets_the_documented_auth_server() -> None:
    assert zepto.CONFIG.name == "zepto"
    assert zepto.CONFIG.issuer == "https://auth.zepto.co.in"
    assert zepto.CONFIG.mcp_base_url == "https://mcp.zepto.co.in"


class _FakePostgrest:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = defaultdict(list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        rows = self.tables[table]
        if request.method == "GET":
            return httpx.Response(200, json=_filter_rows(rows, request.url.params))
        if request.method == "POST":
            payload = json.loads(request.content)
            payload = payload if isinstance(payload, list) else [payload]
            on_conflict = request.url.params.get("on_conflict")
            for row in payload:
                if on_conflict:
                    keys = on_conflict.split(",")
                    rows[:] = [r for r in rows if any(r.get(k) != row.get(k) for k in keys)]
                rows.append(row)
            return httpx.Response(201, json=payload)
        if request.method == "DELETE":
            matched = _filter_rows(rows, request.url.params)
            rows[:] = [r for r in rows if r not in matched]
            return httpx.Response(200, json=matched)
        raise AssertionError(f"unexpected method {request.method}")


def _filter_rows(rows: list[dict], params: httpx.QueryParams) -> list[dict]:
    result = []
    for row in rows:
        matches = True
        for key, value in params.items():
            if key in ("select", "on_conflict") or not value.startswith("eq."):
                continue
            if str(row.get(key)) != value[len("eq.") :]:
                matches = False
                break
        if matches:
            result.append(row)
    return result


def _fake_zepto_auth_server(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/.well-known/oauth-authorization-server":
        return httpx.Response(200, json=_METADATA)
    if path == "/register":
        return httpx.Response(
            201, json={"client_id": "zepto-cid-1", "client_secret": "zepto-secret-1"}
        )
    if path == "/token":
        form = parse_qs(request.content.decode())
        if form["grant_type"][0] == "authorization_code":
            return httpx.Response(
                200,
                json={
                    "access_token": "zepto-at-1",
                    "refresh_token": "zepto-rt-1",
                    "expires_in": 3600,
                },
            )
    raise AssertionError(f"unexpected request to {path}")


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        supabase_service_role_key="service-role-key",
        token_encryption_key=_TEST_KEY,
        backend_base_url="https://api.overdulge.example",
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    monkeypatch.setattr(engine, "get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


@pytest.fixture
def fake_postgrest(monkeypatch: pytest.MonkeyPatch) -> _FakePostgrest:
    fake = _FakePostgrest()

    def _service_role_client(*, transport=None):
        return db.PostgrestClient(
            "https://project.supabase.co",
            headers={"apikey": "service-role-key", "Authorization": "Bearer service-role-key"},
            transport=httpx.MockTransport(fake.handler),
        )

    monkeypatch.setattr(db, "service_role_client", _service_role_client)
    return fake


def test_start_link_returns_authorization_url_against_zepto_discovery_metadata(
    fake_postgrest: _FakePostgrest,
) -> None:
    transport = httpx.MockTransport(_fake_zepto_auth_server)

    result = engine.start_link(zepto.CONFIG, user_id="user-1", transport=transport)

    parsed = urlsplit(result.authorization_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == _METADATA["authorization_endpoint"]
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["zepto-cid-1"]
    assert query["code_challenge_method"] == ["S256"]

    pending = fake_postgrest.tables["oauth_pending_links"][0]
    assert pending["platform"] == "zepto"
    assert pending["state"] == result.state


def test_callback_with_valid_state_stores_encrypted_tokens_on_success(
    fake_postgrest: _FakePostgrest,
) -> None:
    transport = httpx.MockTransport(_fake_zepto_auth_server)
    started = engine.start_link(zepto.CONFIG, user_id="user-1", transport=transport)

    success = engine.handle_callback(
        zepto.CONFIG, code="auth-code", state=started.state, transport=transport
    )

    assert success is True
    linked = fake_postgrest.tables["linked_accounts"][0]
    assert linked["platform"] == "zepto"
    assert "zepto-at-1" not in linked["tokens_encrypted"]
    assert "zepto-rt-1" not in linked["tokens_encrypted"]

    token_set = engine.decode_tokens(linked["tokens_encrypted"])
    assert token_set.access_token == "zepto-at-1"
    assert token_set.refresh_token == "zepto-rt-1"


def test_callback_with_invalid_state_is_rejected(fake_postgrest: _FakePostgrest) -> None:
    transport = httpx.MockTransport(_fake_zepto_auth_server)

    success = engine.handle_callback(
        zepto.CONFIG, code="auth-code", state="never-issued-state", transport=transport
    )

    assert success is False
    assert fake_postgrest.tables["linked_accounts"] == []


def test_start_link_discovers_metadata_at_the_zepto_well_known_endpoint(
    fake_postgrest: _FakePostgrest,
) -> None:
    """Confirms the RFC 8414 discovery URL is derived from `auth.zepto.co.in`
    (no path suffix), unlike Swiggy's issuer which has an `/auth` path.
    """

    discovery_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            discovery_requests.append(request)
        return _fake_zepto_auth_server(request)

    engine.start_link(zepto.CONFIG, user_id="user-1", transport=httpx.MockTransport(handler))

    assert len(discovery_requests) == 1
    assert discovery_requests[0].url.host == "auth.zepto.co.in"
