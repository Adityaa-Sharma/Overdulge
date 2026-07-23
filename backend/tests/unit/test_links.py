from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import links as links_module
from app.core.config import Settings, get_settings
from app.main import app
from app.oauth import engine

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
_PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
)


@dataclass
class _FakeSigningKey:
    key: bytes


class _FakeJwksClient:
    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(key=_PUBLIC_PEM)


def _make_token(*, user_id: str = "user-123") -> str:
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, _PRIVATE_PEM, algorithm="RS256")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "get_jwks_client", lambda: _FakeJwksClient())


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(frontend_settings_url="https://overdulge.example/settings")
    monkeypatch.setattr(links_module, "get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


client = TestClient(app)


def test_start_link_requires_auth() -> None:
    response = client.post("/api/v1/links/swiggy/start")

    assert response.status_code == 401


def test_start_link_returns_authorization_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        links_module.engine,
        "start_link",
        lambda config, *, user_id: engine.AuthorizationRequest(
            authorization_url="https://auth.example/authorize?x=1", state="s1"
        ),
    )
    token = _make_token()

    response = client.post(
        "/api/v1/links/swiggy/start", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"authorization_url": "https://auth.example/authorize?x=1"}


def test_start_link_returns_authorization_url_for_zepto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        links_module.engine,
        "start_link",
        lambda config, *, user_id: engine.AuthorizationRequest(
            authorization_url="https://auth.zepto.co.in/authorize?x=1", state="s1"
        ),
    )
    token = _make_token()

    response = client.post(
        "/api/v1/links/zepto/start", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"authorization_url": "https://auth.zepto.co.in/authorize?x=1"}


def _raise_oauth_error(message: str, *, permanent: bool):
    def _start_link(config, *, user_id):
        raise engine.OAuthError(message, permanent=permanent)

    return _start_link


def test_start_link_maps_oauth_failure_to_502_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the mapping: discovery/DCR talk to a third party, so
    their failure is an upstream fault. Returning 500 told the operator nothing
    and told the user nothing — this endpoint 500'd in production for days.
    """
    monkeypatch.setattr(
        links_module.engine,
        "start_link",
        _raise_oauth_error("metadata discovery failed (503)", permanent=False),
    )

    response = client.post(
        "/api/v1/links/swiggy/start", headers={"Authorization": f"Bearer {_make_token()}"}
    )

    assert response.status_code == 502
    assert "Swiggy" in response.json()["detail"]


def test_start_link_tells_the_user_to_retry_only_when_retrying_could_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        links_module.engine,
        "start_link",
        _raise_oauth_error("temporarily unreachable", permanent=False),
    )

    detail = client.post(
        "/api/v1/links/zepto/start", headers={"Authorization": f"Bearer {_make_token()}"}
    ).json()["detail"]

    assert "try again" in detail.lower()


def test_start_link_does_not_suggest_retrying_a_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected registration fails identically every time. Telling the user
    to try again sends them round a loop that cannot succeed.
    """
    monkeypatch.setattr(
        links_module.engine,
        "start_link",
        _raise_oauth_error(
            "dynamic client registration failed (400: invalid_redirect_uri)", permanent=True
        ),
    )

    detail = client.post(
        "/api/v1/links/zepto/start", headers={"Authorization": f"Bearer {_make_token()}"}
    ).json()["detail"]

    assert "Zepto" in detail
    assert "retrying won't help" in detail.lower()


def test_start_link_failure_detail_never_leaks_the_upstream_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The internal message can quote a third-party server; it belongs in the
    logs, not in a response body echoed back to the browser.
    """
    monkeypatch.setattr(
        links_module.engine,
        "start_link",
        _raise_oauth_error("registration failed at https://internal.host/register", permanent=True),
    )

    detail = client.post(
        "/api/v1/links/swiggy/start", headers={"Authorization": f"Bearer {_make_token()}"}
    ).json()["detail"]

    assert "internal.host" not in detail


def test_start_link_logs_the_failure_reason(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """Without this the operator is back to guessing — which is exactly the
    position the original bare 500 left us in.
    """
    monkeypatch.setattr(
        links_module.engine,
        "start_link",
        _raise_oauth_error(
            "dynamic client registration failed (400: invalid_redirect_uri)", permanent=True
        ),
    )

    with caplog.at_level("ERROR", logger="overdulge"):
        client.post(
            "/api/v1/links/zepto/start", headers={"Authorization": f"Bearer {_make_token()}"}
        )

    record = next(r for r in caplog.records if r.message == "link start failed")
    assert record.platform == "zepto"
    assert "invalid_redirect_uri" in record.failure_reason


def test_callback_redirects_with_status_ok_for_zepto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links_module.engine, "handle_callback", lambda config, *, code, state: True)

    response = client.get(
        "/api/v1/links/zepto/callback",
        params={"code": "c1", "state": "s1"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert (
        response.headers["location"] == "https://overdulge.example/settings?linked=zepto&status=ok"
    )


def test_start_link_rejects_unknown_platform() -> None:
    token = _make_token()

    response = client.post(
        "/api/v1/links/doordash/start", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404


def test_callback_redirects_with_status_ok_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links_module.engine, "handle_callback", lambda config, *, code, state: True)

    response = client.get(
        "/api/v1/links/swiggy/callback",
        params={"code": "c1", "state": "s1"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert (
        response.headers["location"] == "https://overdulge.example/settings?linked=swiggy&status=ok"
    )


def test_callback_redirects_with_status_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        links_module.engine, "handle_callback", lambda config, *, code, state: False
    )

    response = client.get(
        "/api/v1/links/swiggy/callback",
        params={"code": "c1", "state": "bad"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "https://overdulge.example/settings?linked=swiggy&status=error"
    )


def test_callback_with_authorization_server_error_redirects_with_status_error_and_skips_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_if_called(config, *, code, state):
        raise AssertionError("engine.handle_callback must not be called without a code")

    monkeypatch.setattr(links_module.engine, "handle_callback", _fail_if_called)

    response = client.get(
        "/api/v1/links/swiggy/callback",
        params={"state": "s1", "error": "access_denied"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "https://overdulge.example/settings?linked=swiggy&status=error"
    )


def test_callback_rejects_unknown_platform() -> None:
    response = client.get("/api/v1/links/doordash/callback", params={"code": "c1", "state": "s1"})

    assert response.status_code == 404


def test_callback_is_reachable_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links_module.engine, "handle_callback", lambda config, *, code, state: True)

    response = client.get(
        "/api/v1/links/swiggy/callback",
        params={"code": "c1", "state": "s1"},
        follow_redirects=False,
    )

    assert response.status_code == 302


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_user_client(monkeypatch: pytest.MonkeyPatch):
    captured: list[str] = []
    fake = _FakeClient()

    def _factory(jwt: str) -> _FakeClient:
        captured.append(jwt)
        return fake

    monkeypatch.setattr(links_module, "user_client", _factory)
    return captured, fake


def test_list_links_requires_auth() -> None:
    response = client.get("/api/v1/links")

    assert response.status_code == 401


def test_list_links_returns_status_for_every_supported_platform(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    captured_jwts, fake = fake_user_client
    monkeypatch.setattr(
        links_module.linked_accounts,
        "list_linked_accounts",
        lambda client, *, user_id: [{"platform": "swiggy", "linked_at": "2026-07-01T00:00:00Z"}],
    )
    token = _make_token()

    response = client.get("/api/v1/links", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert {"platform": "swiggy", "linked": True, "linked_at": "2026-07-01T00:00:00Z"} in body
    assert {"platform": "zepto", "linked": False, "linked_at": None} in body
    assert captured_jwts == [token]
    assert fake.closed is True


def test_list_links_never_returns_token_fields(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        links_module.linked_accounts,
        "list_linked_accounts",
        lambda client, *, user_id: [
            {
                "platform": "swiggy",
                "linked_at": "2026-07-01T00:00:00Z",
                "tokens_encrypted": "ct-should-never-leak",
            }
        ],
    )
    token = _make_token()

    response = client.get("/api/v1/links", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    for entry in response.json():
        assert set(entry.keys()) == {"platform", "linked", "linked_at"}
        assert "tokens_encrypted" not in entry.values()


def test_unlink_requires_auth() -> None:
    response = client.delete("/api/v1/links/swiggy")

    assert response.status_code == 401


def test_unlink_deletes_row_and_returns_no_content(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    captured_jwts, fake = fake_user_client
    captured_calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        links_module.linked_accounts,
        "delete_linked_account",
        lambda client, *, user_id, platform: captured_calls.append(
            {"user_id": user_id, "platform": platform}
        ),
    )
    token = _make_token(user_id="user-456")

    response = client.delete("/api/v1/links/swiggy", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204
    assert response.content == b""
    assert captured_calls == [{"user_id": "user-456", "platform": "swiggy"}]
    assert captured_jwts == [token]
    assert fake.closed is True


def test_unlink_never_linked_platform_does_not_error(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        links_module.linked_accounts,
        "delete_linked_account",
        lambda client, *, user_id, platform: None,
    )
    token = _make_token()

    response = client.delete("/api/v1/links/zepto", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204


def test_unlink_rejects_unknown_platform() -> None:
    token = _make_token()

    response = client.delete("/api/v1/links/doordash", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 404
