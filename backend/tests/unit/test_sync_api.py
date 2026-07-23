"""Unit tests for the sync API routes (#53, ADR-0005 §6): `GET
/api/v1/sync/status` and `POST /api/v1/sync/{platform}`. `run_sync_for_account`
(#52) itself is mocked — its behaviour is covered by
`backend/tests/unit/test_sync_cron.py`; this file only asserts the route's
own contract (lock check, 404s, response shape).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
from app.api import sync as sync_module
from app.main import app

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


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_user_client(monkeypatch: pytest.MonkeyPatch):
    captured: list[str] = []
    fake = _FakeClient()

    def _factory(token: str) -> _FakeClient:
        captured.append(token)
        return fake

    monkeypatch.setattr(sync_module, "user_client", _factory)
    return captured, fake


client = TestClient(app)


def test_get_status_requires_auth() -> None:
    response = client.get("/api/v1/sync/status")

    assert response.status_code == 401


def test_trigger_sync_requires_auth() -> None:
    response = client.post("/api/v1/sync/zepto")

    assert response.status_code == 401


def test_get_status_omits_platforms_never_linked(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "list_linked_accounts",
        lambda client, *, user_id: [{"platform": "zepto", "last_sync_at": None, "sync_state": {}}],
    )
    token = _make_token()

    response = client.get("/api/v1/sync/status", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert [entry["platform"] for entry in body] == ["zepto"]


def test_get_status_reports_swiggy_row_under_both_sub_platforms(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "list_linked_accounts",
        lambda client, *, user_id: [
            {
                "platform": "swiggy",
                "last_sync_at": "2026-07-20T09:00:00Z",
                "sync_state": {
                    "orders_captured_last_run": {"swiggy_food": 4, "swiggy_instamart": 1}
                },
            }
        ],
    )
    token = _make_token()

    response = client.get("/api/v1/sync/status", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = {entry["platform"]: entry for entry in response.json()}
    assert set(body.keys()) == {"swiggy_food", "swiggy_instamart"}
    assert body["swiggy_food"]["last_sync_at"] == "2026-07-20T09:00:00Z"
    assert body["swiggy_food"]["orders_captured_last_run"] == 4
    assert body["swiggy_food"]["warning"] is None
    assert body["swiggy_instamart"]["orders_captured_last_run"] == 1
    assert body["swiggy_instamart"]["warning"] is not None
    assert "15" in body["swiggy_instamart"]["warning"]


def test_get_status_never_synced_platform_has_null_last_sync_at_and_captured(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "list_linked_accounts",
        lambda client, *, user_id: [{"platform": "zepto", "last_sync_at": None, "sync_state": {}}],
    )
    token = _make_token()

    response = client.get("/api/v1/sync/status", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    entry = response.json()[0]
    assert entry["last_sync_at"] is None
    assert entry["orders_captured_last_run"] is None


def test_trigger_sync_rejects_unknown_platform(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    token = _make_token()

    response = client.post("/api/v1/sync/doordash", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 404


def test_trigger_sync_returns_404_for_unlinked_platform(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: None,
    )
    token = _make_token()

    response = client.post("/api/v1/sync/zepto", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 404


def test_trigger_sync_returns_409_when_lock_recently_held(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    recent = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: {
            "platform": "zepto",
            "sync_state": {"status": "syncing", "syncing_since": recent},
        },
    )

    def _fail_if_called(client, account, **kwargs):
        raise AssertionError("run_sync_for_account must not run while the lock is held")

    monkeypatch.setattr(sync_module, "run_sync_for_account", _fail_if_called)
    token = _make_token()

    response = client.post("/api/v1/sync/zepto", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 409


def test_trigger_sync_proceeds_when_lock_is_stale(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    account = {
        "platform": "zepto",
        "sync_state": {"status": "syncing", "syncing_since": stale},
    }
    refreshed_state = {
        "status": "idle",
        "last_error": None,
        "syncing_since": None,
        "orders_captured_last_run": {"zepto": 2},
    }
    calls: list[dict] = []
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: (
            {**account, "sync_state": refreshed_state} if calls else account
        ),
    )
    monkeypatch.setattr(
        sync_module, "run_sync_for_account", lambda client, acc, **kw: calls.append(acc)
    )
    token = _make_token()

    response = client.post("/api/v1/sync/zepto", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json() == refreshed_state


def test_trigger_sync_calls_run_sync_for_account_and_returns_resulting_sync_state(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    account = {
        "platform": "zepto",
        "sync_state": {"status": "idle", "syncing_since": None},
    }
    refreshed_state = {
        "status": "idle",
        "last_error": None,
        "syncing_since": None,
        "orders_captured_last_run": {"zepto": 3},
    }
    calls: list[dict] = []

    def _get_linked_account(client, *, user_id, platform):
        return {**account, "sync_state": refreshed_state} if calls else account

    monkeypatch.setattr(sync_module.linked_accounts, "get_linked_account", _get_linked_account)
    monkeypatch.setattr(
        sync_module, "run_sync_for_account", lambda client, acc, **kw: calls.append(acc)
    )
    token = _make_token()

    response = client.post("/api/v1/sync/zepto", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == refreshed_state
    assert calls == [account]


def test_trigger_sync_swiggy_food_and_instamart_both_resolve_to_swiggy_account(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    requested_platforms: list[str] = []
    account = {"platform": "swiggy", "sync_state": {"status": "idle"}}
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: requested_platforms.append(platform) or account,
    )
    monkeypatch.setattr(sync_module, "run_sync_for_account", lambda client, acc, **kw: None)
    token = _make_token()

    for platform in ("swiggy_food", "swiggy_instamart"):
        response = client.post(
            f"/api/v1/sync/{platform}", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    assert requested_platforms == ["swiggy", "swiggy", "swiggy", "swiggy"]


def test_trigger_sync_closes_client(monkeypatch: pytest.MonkeyPatch, fake_user_client) -> None:
    captured_jwts, fake = fake_user_client
    account = {"platform": "zepto", "sync_state": {"status": "idle"}}
    monkeypatch.setattr(
        sync_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: {**account, "sync_state": {"status": "idle"}},
    )
    monkeypatch.setattr(sync_module, "run_sync_for_account", lambda client, acc, **kw: None)
    token = _make_token()

    response = client.post("/api/v1/sync/zepto", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert captured_jwts == [token]
    assert fake.closed is True
