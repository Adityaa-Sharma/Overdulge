import base64
import json
from collections import defaultdict

import httpx
import pytest

from app.core import crypto, db
from app.core.config import Settings, get_settings
from app.oauth import engine
from app.sync import cron

_TEST_KEY = base64.b64encode(b"0" * 32).decode("ascii")
_FUTURE = "2099-01-01T00:00:00+00:00"
_PAST = "2020-01-01T00:00:00+00:00"

_OAUTH_METADATA = {
    # These fixtures sync the "zepto" platform, so the document has to claim
    # Zepto's issuer — the engine discards metadata that names a different one.
    "issuer": "https://auth.zepto.co.in",
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": "https://auth.example.com/oauth/token",
    "registration_endpoint": "https://auth.example.com/oauth/register",
}


class FakePostgrest:
    """In-memory PostgREST stand-in.

    POST models `resolution=merge-duplicates` (merge the given keys into a
    matching row, else insert). PATCH models a partial UPDATE of the rows
    matching the query filters — which is how the sync_state/token helpers now
    write, after a partial *upsert* was found to fail 23502 against the real DB
    (it omits the NOT NULL tokens_encrypted column, which Postgres validates
    before ON CONFLICT can resolve).
    """

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = defaultdict(list)
        self._next_id = 0

    def _new_id(self) -> str:
        self._next_id += 1
        return f"row-{self._next_id}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        rows = self.tables[table]
        if request.method == "GET":
            return httpx.Response(200, json=_filter_rows(rows, request.url.params))
        if request.method == "POST":
            payload = json.loads(request.content)
            payload = payload if isinstance(payload, list) else [payload]
            on_conflict = request.url.params.get("on_conflict")
            result_rows = []
            for row in payload:
                existing = None
                if on_conflict:
                    keys = on_conflict.split(",")
                    existing = next(
                        (r for r in rows if all(r.get(k) == row.get(k) for k in keys)), None
                    )
                if existing is not None:
                    existing.update(row)
                    result_rows.append(existing)
                    continue
                new_row = dict(row)
                new_row.setdefault("id", self._new_id())
                rows.append(new_row)
                result_rows.append(new_row)
            return httpx.Response(201, json=result_rows)
        if request.method == "PATCH":
            values = json.loads(request.content)
            matched = _filter_rows(rows, request.url.params)
            for row in matched:
                row.update(values)
            return httpx.Response(200, json=matched)
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


def _tokens_encrypted(*, access_token: str, refresh_token: str = "rt-1", expires_at=None) -> str:
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
    }
    return crypto.encrypt(json.dumps(payload).encode("utf-8"))


def _seed_linked_account(
    fake_postgrest,
    *,
    user_id: str = "user-1",
    platform: str,
    access_token: str = "at-1",
    expires_at: str | None = _FUTURE,
    sync_state: dict | None = None,
) -> dict:
    row = {
        "user_id": user_id,
        "platform": platform,
        "tokens_encrypted": _tokens_encrypted(access_token=access_token, expires_at=expires_at),
        "sync_state": sync_state if sync_state is not None else {},
        "last_sync_at": None,
    }
    fake_postgrest.tables["linked_accounts"].append(row)
    return row


def _oauth_and_mcp_transport(mcp_responses: dict[str, dict], *, token_calls: list | None = None):
    token_calls = token_calls if token_calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(200, json=_OAUTH_METADATA)
        if path == "/oauth/register":
            return httpx.Response(201, json={"client_id": "cid-1", "client_secret": "secret-1"})
        if path == "/oauth/token":
            token_calls.append(1)
            return httpx.Response(
                200,
                json={
                    "access_token": "at-refreshed",
                    "refresh_token": "rt-refreshed",
                    "expires_in": 3600,
                },
            )
        body = json.loads(request.content)
        tool_name = body["params"]["name"]
        if tool_name not in mcp_responses:
            raise AssertionError(f"unexpected tool call: {tool_name}")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": mcp_responses[tool_name]}
        )

    return httpx.MockTransport(handler), token_calls


_FOOD_RESPONSES = {
    "get_addresses": {"addresses": [{"addressId": "addr1"}]},
    "get_food_orders": {
        "orders": [
            {
                "orderId": "f1",
                "orderStatus": "DELIVERED",
                "orderTime": "February 1, 1:00 PM",
                "grandTotal": "₹100",
                "restaurantName": "R1",
            }
        ]
    },
    "get_food_order_details": {"orderId": "f1", "orderTime": "2026-02-01T13:00:00+05:30"},
}

_INSTAMART_RESPONSES = {
    "get_orders": {
        "orders": [
            {
                "orderId": "i1",
                "status": "DELIVERED",
                "orderTime": "2026-02-01T08:00:00Z",
                "grandTotal": 50,
                "storeName": "S1",
            }
        ]
    }
}

_ZEPTO_RESPONSES = {
    "list_order_history": {"orders": [{"orderId": "z1", "status": "DELIVERED"}]},
    "get_order_detail": {
        "orderId": "z1",
        "status": "DELIVERED",
        "orderedAt": "2026-02-01T09:00:00Z",
        "grandTotal": 3000,
    },
}


def test_swiggy_account_fans_out_to_food_and_instamart(fake_postgrest):
    account = _seed_linked_account(fake_postgrest, platform="swiggy")
    transport, _ = _oauth_and_mcp_transport({**_FOOD_RESPONSES, **_INSTAMART_RESPONSES})
    client = db.service_role_client()

    result = cron.run_sync_for_account(client, account, transport=transport)

    assert result.success is True
    assert result.orders_captured == {"swiggy_food": 1, "swiggy_instamart": 1}
    platforms = {row["platform"] for row in fake_postgrest.tables["orders"]}
    assert platforms == {"swiggy_food", "swiggy_instamart"}


def test_swiggy_tool_calls_target_the_per_surface_mcp_paths(fake_postgrest):
    # Regression: both surfaces were sent to the bare host, which serves no
    # tool server. Food must go to /food, Instamart to /im.
    account = _seed_linked_account(fake_postgrest, platform="swiggy")
    tool_paths: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/.well-known") or request.url.path.startswith("/oauth"):
            return _oauth_and_mcp_transport({})[0].handler(request)
        body = json.loads(request.content)
        tool_paths[body["params"]["name"]] = request.url.path
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {**_FOOD_RESPONSES, **_INSTAMART_RESPONSES}[body["params"]["name"]],
            },
        )

    cron.run_sync_for_account(
        db.service_role_client(), account, transport=httpx.MockTransport(handler)
    )

    assert tool_paths["get_addresses"] == "/food"
    assert tool_paths["get_food_orders"] == "/food"
    assert tool_paths["get_orders"] == "/im"


def test_zepto_tool_calls_target_the_mcp_path(fake_postgrest):
    account = _seed_linked_account(fake_postgrest, platform="zepto")
    tool_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/.well-known") or request.url.path.startswith("/oauth"):
            return _oauth_and_mcp_transport({})[0].handler(request)
        body = json.loads(request.content)
        tool_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": _ZEPTO_RESPONSES[body["params"]["name"]]},
        )

    cron.run_sync_for_account(
        db.service_role_client(), account, transport=httpx.MockTransport(handler)
    )

    assert set(tool_paths) == {"/mcp"}


def test_zepto_account_syncs_single_sub_platform(fake_postgrest):
    account = _seed_linked_account(fake_postgrest, platform="zepto")
    transport, _ = _oauth_and_mcp_transport(_ZEPTO_RESPONSES)
    client = db.service_role_client()

    result = cron.run_sync_for_account(client, account, transport=transport)

    assert result.success is True
    assert result.orders_captured == {"zepto": 1}
    assert [row["platform"] for row in fake_postgrest.tables["orders"]] == ["zepto"]


def test_sync_state_transitions_correctly_on_success(fake_postgrest):
    account = _seed_linked_account(fake_postgrest, platform="zepto")
    transport, _ = _oauth_and_mcp_transport(_ZEPTO_RESPONSES)
    client = db.service_role_client()

    cron.run_sync_for_account(client, account, transport=transport)

    stored = fake_postgrest.tables["linked_accounts"][0]
    assert stored["sync_state"]["status"] == "idle"
    assert stored["sync_state"]["syncing_since"] is None
    assert stored["sync_state"]["last_error"] is None
    assert stored["sync_state"]["orders_captured_last_run"] == {"zepto": 1}
    assert stored["last_sync_at"] is not None


def test_sync_state_transitions_correctly_on_failure_and_last_sync_at_untouched(fake_postgrest):
    account = _seed_linked_account(fake_postgrest, platform="zepto")

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/.well-known"):
            return httpx.Response(200, json=_OAUTH_METADATA)
        return httpx.Response(500, text="mcp server unavailable")

    transport = httpx.MockTransport(failing_handler)
    client = db.service_role_client()

    result = cron.run_sync_for_account(client, account, transport=transport)

    assert result.success is False
    assert result.error is not None
    stored = fake_postgrest.tables["linked_accounts"][0]
    assert stored["sync_state"]["status"] == "idle"
    assert stored["sync_state"]["syncing_since"] is None
    assert stored["sync_state"]["last_error"] is not None
    assert stored["last_sync_at"] is None


def test_expired_token_triggers_refresh_exactly_once_before_adapter_call(fake_postgrest):
    account = _seed_linked_account(fake_postgrest, platform="zepto", expires_at=_PAST)
    seen_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/.well-known"):
            return httpx.Response(200, json=_OAUTH_METADATA)
        if path == "/oauth/register":
            return httpx.Response(201, json={"client_id": "cid-1", "client_secret": "secret-1"})
        if path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "at-refreshed",
                    "refresh_token": "rt-refreshed",
                    "expires_in": 3600,
                },
            )
        seen_tokens.append(request.headers["authorization"])
        body = json.loads(request.content)
        tool_name = body["params"]["name"]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": _ZEPTO_RESPONSES[tool_name]}
        )

    transport = httpx.MockTransport(handler)
    client = db.service_role_client()

    result = cron.run_sync_for_account(client, account, transport=transport)

    assert result.success is True
    assert all(token == "Bearer at-refreshed" for token in seen_tokens)
    stored = fake_postgrest.tables["linked_accounts"][0]
    refreshed = engine.decode_tokens(stored["tokens_encrypted"])
    assert refreshed.access_token == "at-refreshed"
    assert refreshed.refresh_token == "rt-refreshed"


def test_state_write_failure_does_not_stop_daily_sync_loop(fake_postgrest):
    _seed_linked_account(fake_postgrest, user_id="user-bad-write", platform="zepto")
    _seed_linked_account(fake_postgrest, user_id="user-good", platform="zepto")

    real_handler = fake_postgrest.handler

    def flaky_handler(request: httpx.Request) -> httpx.Response:
        # sync_state is written with PATCH (filtered by user_id in the query),
        # so that is where a state-write failure now surfaces.
        if request.method == "PATCH" and request.url.path.endswith("/linked_accounts"):
            if request.url.params.get("user_id") == "eq.user-bad-write":
                return httpx.Response(500, text="linked_accounts update unavailable")
        return real_handler(request)

    fake_postgrest.handler = flaky_handler
    transport, _ = _oauth_and_mcp_transport(_ZEPTO_RESPONSES)

    results = cron.run_daily_sync(transport=transport)

    assert len(results) == 2
    by_user = {result.user_id: result for result in results}
    assert by_user["user-bad-write"].success is False
    assert by_user["user-bad-write"].error is not None
    assert by_user["user-good"].success is True
    assert by_user["user-good"].orders_captured == {"zepto": 1}


def test_failing_account_does_not_stop_daily_sync_loop(fake_postgrest):
    _seed_linked_account(
        fake_postgrest, user_id="user-bad", platform="zepto", access_token="bad-token"
    )
    _seed_linked_account(
        fake_postgrest, user_id="user-good", platform="zepto", access_token="good-token"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/.well-known"):
            return httpx.Response(200, json=_OAUTH_METADATA)
        if request.headers.get("authorization") == "Bearer bad-token":
            return httpx.Response(500, text="mcp server unavailable")
        body = json.loads(request.content)
        tool_name = body["params"]["name"]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": _ZEPTO_RESPONSES[tool_name]}
        )

    transport = httpx.MockTransport(handler)

    results = cron.run_daily_sync(transport=transport)

    assert len(results) == 2
    by_user = {result.user_id: result for result in results}
    assert by_user["user-bad"].success is False
    assert by_user["user-good"].success is True
    assert by_user["user-good"].orders_captured == {"zepto": 1}
