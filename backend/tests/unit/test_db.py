import httpx
import pytest

from app.core import db
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


def _capturing_transport(captured: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"id": 1}])

    return httpx.MockTransport(handler)


def test_service_role_client_sends_service_role_headers():
    captured: list[httpx.Request] = []
    client = db.service_role_client(transport=_capturing_transport(captured))

    client.select("orders")

    assert len(captured) == 1
    request = captured[0]
    assert request.headers["apikey"] == "service-role-key"
    assert request.headers["Authorization"] == "Bearer service-role-key"
    assert request.url.path == "/rest/v1/orders"


def test_user_client_sends_anon_apikey_and_forwards_user_jwt():
    captured: list[httpx.Request] = []
    client = db.user_client("caller-jwt", transport=_capturing_transport(captured))

    client.select("orders")

    assert len(captured) == 1
    request = captured[0]
    assert request.headers["apikey"] == "anon-key"
    assert request.headers["Authorization"] == "Bearer caller-jwt"


def test_service_role_client_requires_configured_credentials(monkeypatch):
    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(supabase_url=None, supabase_service_role_key=None),
    )

    with pytest.raises(RuntimeError):
        db.service_role_client()


def test_user_client_requires_configured_credentials(monkeypatch):
    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(supabase_url=None, supabase_anon_key=None),
    )

    with pytest.raises(RuntimeError):
        db.user_client("caller-jwt")


def test_select_builds_query_params_from_columns_and_filters():
    captured: list[httpx.Request] = []
    client = db.service_role_client(transport=_capturing_transport(captured))

    client.select("orders", columns="id,total", filters={"user_id": "eq.42"})

    request = captured[0]
    assert request.url.params["select"] == "id,total"
    assert request.url.params["user_id"] == "eq.42"


def test_insert_sends_representation_preference():
    captured: list[httpx.Request] = []
    client = db.service_role_client(transport=_capturing_transport(captured))

    client.insert("orders", {"id": 1})

    request = captured[0]
    assert request.method == "POST"
    assert request.headers["Prefer"] == "return=representation"


def test_upsert_sends_on_conflict_and_merge_preference():
    captured: list[httpx.Request] = []
    client = db.service_role_client(transport=_capturing_transport(captured))

    client.upsert("orders", {"id": 1}, on_conflict="id")

    request = captured[0]
    assert request.method == "POST"
    assert request.url.params["on_conflict"] == "id"
    assert request.headers["Prefer"] == "resolution=merge-duplicates,return=representation"


def test_delete_sends_filters_and_representation_preference():
    captured: list[httpx.Request] = []
    client = db.service_role_client(transport=_capturing_transport(captured))

    client.delete("orders", filters={"id": "eq.1"})

    request = captured[0]
    assert request.method == "DELETE"
    assert request.url.params["id"] == "eq.1"
    assert request.headers["Prefer"] == "return=representation"


def test_raises_postgrest_error_on_failed_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, text="conflict")

    client = db.service_role_client(transport=httpx.MockTransport(handler))

    with pytest.raises(db.PostgrestError) as excinfo:
        client.select("orders")

    assert excinfo.value.status_code == 409
