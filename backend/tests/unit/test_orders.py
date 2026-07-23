import httpx
import pytest

from app.core import db, orders
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


def test_list_orders_filters_by_user_only_by_default():
    captured: list[httpx.Request] = []
    client = _client_returning([{"id": "o1"}, {"id": "o2"}], captured)

    result = orders.list_orders(client, user_id="u1")

    assert len(result) == 2
    request = captured[0]
    assert request.url.params["user_id"] == "eq.u1"
    assert "platform" not in request.url.params
    assert "is_cancelled" not in request.url.params


def test_list_orders_filters_by_platform_and_is_cancelled_when_given():
    captured: list[httpx.Request] = []
    client = _client_returning([{"id": "o1"}], captured)

    orders.list_orders(client, user_id="u1", platform="zepto", is_cancelled=False)

    request = captured[0]
    assert request.url.params["user_id"] == "eq.u1"
    assert request.url.params["platform"] == "eq.zepto"
    assert request.url.params["is_cancelled"] == "eq.false"


def test_get_order_filters_by_platform_and_platform_order_id():
    captured: list[httpx.Request] = []
    client = _client_returning([{"platform": "swiggy_food", "platform_order_id": "p1"}], captured)

    result = orders.get_order(client, platform="swiggy_food", platform_order_id="p1")

    assert result == {"platform": "swiggy_food", "platform_order_id": "p1"}
    request = captured[0]
    assert request.url.params["platform"] == "eq.swiggy_food"
    assert request.url.params["platform_order_id"] == "eq.p1"


def test_get_order_returns_none_when_absent():
    client = _client_returning([], [])

    result = orders.get_order(client, platform="zepto", platform_order_id="missing")

    assert result is None


def test_list_order_items_filters_by_order_id():
    captured: list[httpx.Request] = []
    client = _client_returning([{"name": "item"}], captured)

    result = orders.list_order_items(client, order_id="o1")

    assert result == [{"name": "item"}]
    assert captured[0].url.params["order_id"] == "eq.o1"


def test_list_order_items_for_orders_filters_with_in_clause():
    captured: list[httpx.Request] = []
    client = _client_returning([{"name": "item"}], captured)

    result = orders.list_order_items_for_orders(client, order_ids=["o1", "o2"])

    assert result == [{"name": "item"}]
    assert captured[0].url.params["order_id"] == "in.(o1,o2)"


def test_list_order_items_for_orders_skips_request_when_no_order_ids():
    captured: list[httpx.Request] = []
    client = _client_returning([{"name": "item"}], captured)

    result = orders.list_order_items_for_orders(client, order_ids=[])

    assert result == []
    assert captured == []
