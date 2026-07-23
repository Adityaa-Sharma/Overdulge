import base64
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core import crypto, db
from app.core.config import Settings, get_settings
from app.oauth import engine
from app.oauth.engine import PlatformConfig

_TEST_KEY = base64.b64encode(b"0" * 32).decode("ascii")

TEST_CONFIG = PlatformConfig(
    name="testplatform",
    issuer="https://auth.example.com/oauth",
    scopes=("orders:read",),
    mcp_base_url="https://mcp.example.com",
)

_METADATA = {
    "authorization_endpoint": "https://auth.example.com/oauth/authorize",
    "token_endpoint": "https://auth.example.com/oauth/token",
    "registration_endpoint": "https://auth.example.com/oauth/register",
}


class FakePostgrest:
    """In-memory stand-in for PostgREST, enough to exercise engine.py's
    upsert-on-conflict / filtered-select / delete calls against
    oauth_pending_links, oauth_clients and linked_accounts.
    """

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


class FakeAuthServer:
    """Mocked authorization server: metadata discovery, DCR registration,
    and the token endpoint (authorization_code + refresh_token grants).
    """

    def __init__(self) -> None:
        self.registration_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-authorization-server/oauth":
            return httpx.Response(200, json=_METADATA)
        if path == "/oauth/register":
            self.registration_calls += 1
            return httpx.Response(201, json={"client_id": "cid-1", "client_secret": "secret-1"})
        if path == "/oauth/token":
            form = parse_qs(request.content.decode())
            grant_type = form["grant_type"][0]
            if grant_type == "authorization_code":
                return httpx.Response(
                    200,
                    json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
                )
            if grant_type == "refresh_token":
                return httpx.Response(
                    200,
                    json={"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600},
                )
        raise AssertionError(f"unexpected request to {path}")


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
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
def fake_postgrest(monkeypatch):
    fake = FakePostgrest()

    def _service_role_client(*, transport=None):
        return db.PostgrestClient(
            "https://project.supabase.co",
            headers={"apikey": "service-role-key", "Authorization": "Bearer service-role-key"},
            transport=httpx.MockTransport(fake.handler),
        )

    monkeypatch.setattr(db, "service_role_client", _service_role_client)
    return fake


@pytest.fixture
def fake_auth_server():
    return FakeAuthServer()


def test_start_link_returns_authorization_url_with_correct_pkce_and_state(
    fake_postgrest, fake_auth_server
):
    transport = httpx.MockTransport(fake_auth_server.handler)

    result = engine.start_link(TEST_CONFIG, user_id="user-1", transport=transport)

    parsed = urlsplit(result.authorization_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == _METADATA["authorization_endpoint"]
    query = parse_qs(parsed.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid-1"]
    assert query["state"] == [result.state]
    assert query["code_challenge_method"] == ["S256"]

    pending = fake_postgrest.tables["oauth_pending_links"][0]
    assert pending["user_id"] == "user-1"
    assert pending["platform"] == "testplatform"
    assert pending["state"] == result.state
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(pending["code_verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge"] == [expected_challenge]

    expires_at = datetime.fromisoformat(pending["expires_at"])
    now = datetime.now(UTC)
    assert now < expires_at < now + timedelta(minutes=11)


def test_start_link_reuses_cached_dcr_registration(fake_postgrest, fake_auth_server):
    transport = httpx.MockTransport(fake_auth_server.handler)

    engine.start_link(TEST_CONFIG, user_id="user-1", transport=transport)
    engine.start_link(TEST_CONFIG, user_id="user-2", transport=transport)

    assert fake_auth_server.registration_calls == 1
    oauth_client = fake_postgrest.tables["oauth_clients"][0]
    assert "secret-1" not in oauth_client["client_secret_encrypted"]


def test_callback_with_valid_state_stores_encrypted_tokens_and_returns_true(
    fake_postgrest, fake_auth_server
):
    transport = httpx.MockTransport(fake_auth_server.handler)
    started = engine.start_link(TEST_CONFIG, user_id="user-1", transport=transport)

    success = engine.handle_callback(
        TEST_CONFIG, code="auth-code", state=started.state, transport=transport
    )

    assert success is True
    assert fake_postgrest.tables["oauth_pending_links"] == []
    linked = fake_postgrest.tables["linked_accounts"][0]
    assert linked["user_id"] == "user-1"
    assert linked["platform"] == "testplatform"
    assert "at-1" not in linked["tokens_encrypted"]
    assert "rt-1" not in linked["tokens_encrypted"]

    token_set = engine.decode_tokens(linked["tokens_encrypted"])
    assert token_set.access_token == "at-1"
    assert token_set.refresh_token == "rt-1"
    assert token_set.expires_at is not None


def test_callback_with_unknown_state_returns_false_and_touches_nothing(
    fake_postgrest, fake_auth_server
):
    transport = httpx.MockTransport(fake_auth_server.handler)

    success = engine.handle_callback(
        TEST_CONFIG, code="auth-code", state="unknown-state", transport=transport
    )

    assert success is False
    assert fake_postgrest.tables["linked_accounts"] == []


def test_callback_with_expired_state_returns_false_and_clears_pending_row(
    fake_postgrest, fake_auth_server
):
    transport = httpx.MockTransport(fake_auth_server.handler)
    started = engine.start_link(TEST_CONFIG, user_id="user-1", transport=transport)
    fake_postgrest.tables["oauth_pending_links"][0]["expires_at"] = (
        datetime.now(UTC) - timedelta(minutes=1)
    ).isoformat()

    success = engine.handle_callback(
        TEST_CONFIG, code="auth-code", state=started.state, transport=transport
    )

    assert success is False
    assert fake_postgrest.tables["linked_accounts"] == []
    assert fake_postgrest.tables["oauth_pending_links"] == []


def test_callback_with_platform_mismatch_returns_false(fake_postgrest, fake_auth_server):
    transport = httpx.MockTransport(fake_auth_server.handler)
    started = engine.start_link(TEST_CONFIG, user_id="user-1", transport=transport)
    other_config = PlatformConfig(
        name="other", issuer=TEST_CONFIG.issuer, scopes=(), mcp_base_url="https://mcp.example.com"
    )

    success = engine.handle_callback(
        other_config, code="auth-code", state=started.state, transport=transport
    )

    assert success is False
    assert fake_postgrest.tables["linked_accounts"] == []


def test_callback_with_failed_token_exchange_returns_false_and_clears_pending_row(
    fake_postgrest,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server/oauth":
            return httpx.Response(200, json=_METADATA)
        if request.url.path == "/oauth/register":
            return httpx.Response(201, json={"client_id": "cid-1", "client_secret": "secret-1"})
        if request.url.path == "/oauth/token":
            return httpx.Response(400, text="invalid_grant")
        raise AssertionError(request.url.path)

    transport = httpx.MockTransport(handler)
    started = engine.start_link(TEST_CONFIG, user_id="user-1", transport=transport)

    success = engine.handle_callback(
        TEST_CONFIG, code="bad-code", state=started.state, transport=transport
    )

    assert success is False
    assert fake_postgrest.tables["linked_accounts"] == []
    assert fake_postgrest.tables["oauth_pending_links"] == []


def test_callback_with_metadata_discovery_failure_returns_false_and_clears_pending_row(
    fake_postgrest,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server/oauth":
            return httpx.Response(503, text="service unavailable")
        raise AssertionError(request.url.path)

    registration_transport = httpx.MockTransport(FakeAuthServer().handler)
    started = engine.start_link(TEST_CONFIG, user_id="user-1", transport=registration_transport)

    success = engine.handle_callback(
        TEST_CONFIG, code="auth-code", state=started.state, transport=httpx.MockTransport(handler)
    )

    assert success is False
    assert fake_postgrest.tables["linked_accounts"] == []
    assert fake_postgrest.tables["oauth_pending_links"] == []


def test_refresh_tokens_rotates_access_and_refresh_token(fake_postgrest, fake_auth_server):
    transport = httpx.MockTransport(fake_auth_server.handler)

    token_set = engine.refresh_tokens(TEST_CONFIG, refresh_token="rt-old", transport=transport)

    assert token_set.access_token == "at-2"
    assert token_set.refresh_token == "rt-2"
    assert token_set.expires_at is not None


def test_refresh_tokens_keeps_prior_refresh_token_when_platform_omits_one(fake_postgrest):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server/oauth":
            return httpx.Response(200, json=_METADATA)
        if request.url.path == "/oauth/register":
            return httpx.Response(201, json={"client_id": "cid-1", "client_secret": "secret-1"})
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "at-3", "expires_in": 3600})
        raise AssertionError(request.url.path)

    token_set = engine.refresh_tokens(
        TEST_CONFIG, refresh_token="rt-keep", transport=httpx.MockTransport(handler)
    )

    assert token_set.access_token == "at-3"
    assert token_set.refresh_token == "rt-keep"


def test_refresh_tokens_raises_on_token_endpoint_error(fake_postgrest):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server/oauth":
            return httpx.Response(200, json=_METADATA)
        if request.url.path == "/oauth/register":
            return httpx.Response(201, json={"client_id": "cid-1", "client_secret": "secret-1"})
        if request.url.path == "/oauth/token":
            return httpx.Response(401, text="invalid_grant")
        raise AssertionError(request.url.path)

    with pytest.raises(engine.OAuthError):
        engine.refresh_tokens(
            TEST_CONFIG, refresh_token="rt-old", transport=httpx.MockTransport(handler)
        )
