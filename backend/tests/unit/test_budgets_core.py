import json

import httpx
import pytest

from app.core import budgets, db
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


def test_list_budgets_filters_by_user_and_month():
    captured: list[httpx.Request] = []
    client = _client_returning(
        [{"user_id": "u1", "month": "2026-07-01", "category": "__overall__", "cap_paise": 500000}],
        captured,
    )

    result = budgets.list_budgets(client, user_id="u1", month="2026-07-01")

    assert len(result) == 1
    assert result[0]["category"] is None
    request = captured[0]
    assert request.url.params["user_id"] == "eq.u1"
    assert request.url.params["month"] == "eq.2026-07-01"


def test_upsert_budget_uses_single_index_when_category_set():
    captured: list[httpx.Request] = []
    client = _client_returning(
        [{"user_id": "u1", "month": "2026-07-01", "category": "food", "cap_paise": 100000}],
        captured,
    )

    result = budgets.upsert_budget(
        client, user_id="u1", month="2026-07-01", category="food", cap_paise=100000
    )

    assert result["category"] == "food"
    request = captured[0]
    assert request.method == "POST"
    assert request.url.params["on_conflict"] == "user_id,month,category"
    body = json.loads(request.content)
    assert body == {
        "user_id": "u1",
        "month": "2026-07-01",
        "category": "food",
        "cap_paise": 100000,
    }


def test_upsert_budget_stores_sentinel_and_translates_back_when_category_none():
    captured: list[httpx.Request] = []
    client = _client_returning(
        [{"user_id": "u1", "month": "2026-07-01", "category": "__overall__", "cap_paise": 500000}],
        captured,
    )

    result = budgets.upsert_budget(
        client, user_id="u1", month="2026-07-01", category=None, cap_paise=500000
    )

    assert result["category"] is None
    request = captured[0]
    assert request.url.params["on_conflict"] == "user_id,month,category"
    body = json.loads(request.content)
    assert body["category"] == "__overall__"
    assert body["cap_paise"] == 500000


def test_upsert_budget_rejects_sentinel_as_a_real_category():
    client = _client_returning([], [])

    with pytest.raises(ValueError):
        budgets.upsert_budget(
            client, user_id="u1", month="2026-07-01", category="__overall__", cap_paise=100000
        )


def test_delete_budget_filters_by_id():
    captured: list[httpx.Request] = []
    client = _client_returning([], captured)

    budgets.delete_budget(client, id="b1")

    request = captured[0]
    assert request.method == "DELETE"
    assert request.url.params["id"] == "eq.b1"
