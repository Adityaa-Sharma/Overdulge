import json

import httpx2 as httpx
import pytest

from app.core import db, linked_accounts
from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
    settings = Settings(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        supabase_service_role_key="service-role-key",
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


def _client_returning(rows: list[dict], captured: list[httpx.Request]) -> db.PostgrestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=rows)

    return db.service_role_client(transport=httpx.MockTransport(handler))


def test_get_linked_account_filters_by_user_and_platform():
    captured: list[httpx.Request] = []
    client = _client_returning([{"user_id": "u1", "platform": "swiggy"}], captured)

    result = linked_accounts.get_linked_account(client, user_id="u1", platform="swiggy")

    assert result == {"user_id": "u1", "platform": "swiggy"}
    request = captured[0]
    assert request.url.params["user_id"] == "eq.u1"
    assert request.url.params["platform"] == "eq.swiggy"


def test_get_linked_account_returns_none_when_absent():
    client = _client_returning([], [])

    result = linked_accounts.get_linked_account(client, user_id="u1", platform="swiggy")

    assert result is None


def test_list_linked_accounts_filters_by_user_only():
    captured: list[httpx.Request] = []
    client = _client_returning([{"platform": "swiggy"}, {"platform": "zepto"}], captured)

    result = linked_accounts.list_linked_accounts(client, user_id="u1")

    assert len(result) == 2
    assert captured[0].url.params["user_id"] == "eq.u1"


def test_upsert_linked_account_sends_on_conflict_and_payload():
    captured: list[httpx.Request] = []
    client = _client_returning([{"user_id": "u1", "platform": "swiggy"}], captured)

    linked_accounts.upsert_linked_account(
        client, user_id="u1", platform="swiggy", tokens_encrypted="ct"
    )

    request = captured[0]
    assert request.method == "POST"
    assert request.url.params["on_conflict"] == "user_id,platform"
    body = json.loads(request.content)
    assert body == {
        "user_id": "u1",
        "platform": "swiggy",
        "tokens_encrypted": "ct",
        "sync_state": {},
    }


def test_delete_linked_account_filters_by_user_and_platform():
    captured: list[httpx.Request] = []
    client = _client_returning([], captured)

    linked_accounts.delete_linked_account(client, user_id="u1", platform="swiggy")

    request = captured[0]
    assert request.method == "DELETE"
    assert request.url.params["user_id"] == "eq.u1"
    assert request.url.params["platform"] == "eq.swiggy"


def test_upsert_pending_link_overwrites_via_on_conflict():
    captured: list[httpx.Request] = []
    client = _client_returning([{"state": "s1"}], captured)

    linked_accounts.upsert_pending_link(
        client,
        user_id="u1",
        platform="swiggy",
        code_verifier="verifier",
        state="s1",
        expires_at="2026-07-22T00:00:00Z",
    )

    request = captured[0]
    assert request.url.params["on_conflict"] == "user_id,platform"
    body = json.loads(request.content)
    assert body["state"] == "s1"
    assert body["code_verifier"] == "verifier"


def test_get_pending_link_by_state_filters_by_state():
    captured: list[httpx.Request] = []
    client = _client_returning([{"state": "s1"}], captured)

    result = linked_accounts.get_pending_link_by_state(client, state="s1")

    assert result == {"state": "s1"}
    assert captured[0].url.params["state"] == "eq.s1"


def test_delete_pending_link_filters_by_user_and_platform():
    captured: list[httpx.Request] = []
    client = _client_returning([], captured)

    linked_accounts.delete_pending_link(client, user_id="u1", platform="zepto")

    request = captured[0]
    assert request.method == "DELETE"
    assert request.url.params["user_id"] == "eq.u1"
    assert request.url.params["platform"] == "eq.zepto"


def test_get_oauth_client_filters_by_platform():
    captured: list[httpx.Request] = []
    client = _client_returning([{"platform": "swiggy"}], captured)

    result = linked_accounts.get_oauth_client(client, platform="swiggy")

    assert result == {"platform": "swiggy"}
    assert captured[0].url.params["platform"] == "eq.swiggy"


def test_get_oauth_client_returns_none_when_absent():
    client = _client_returning([], [])

    result = linked_accounts.get_oauth_client(client, platform="swiggy")

    assert result is None


def test_upsert_oauth_client_sends_on_conflict_platform():
    captured: list[httpx.Request] = []
    client = _client_returning([{"platform": "swiggy"}], captured)

    linked_accounts.upsert_oauth_client(
        client,
        platform="swiggy",
        client_id="cid",
        client_secret_encrypted="enc",
    )

    request = captured[0]
    assert request.url.params["on_conflict"] == "platform"
    body = json.loads(request.content)
    assert body == {
        "platform": "swiggy",
        "client_id": "cid",
        "client_secret_encrypted": "enc",
        "expires_at": None,
    }
