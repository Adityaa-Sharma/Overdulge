from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
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
from app.api import budgets as budgets_module
from app.core import db as db_module
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


client = TestClient(app)


class _FakePostgrest:
    """Fixture rows per table, plus every request the route issued — lets
    tests assert both response shape and exactly what PostgREST calls a
    route made (e.g. that DELETE never touches `orders`/`order_items`).
    """

    def __init__(self, fixtures: dict[str, list[dict]]) -> None:
        self.fixtures = fixtures
        self.requests: list[httpx.Request] = []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        if request.method == "POST":
            body = json.loads(request.content)
            rows = body if isinstance(body, list) else [body]
            return httpx.Response(201, json=[{"id": "generated-id", **row} for row in rows])
        if request.method == "DELETE":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=self.fixtures.get(table, []))

    def client_factory(self, jwt_token: str) -> db_module.PostgrestClient:
        return db_module.PostgrestClient(
            "https://project.supabase.co",
            headers={"apikey": "anon-key", "Authorization": f"Bearer {jwt_token}"},
            transport=httpx.MockTransport(self._handler),
        )


@pytest.fixture
def fake_postgrest(monkeypatch: pytest.MonkeyPatch):
    fake = _FakePostgrest(fixtures={})

    def _factory(jwt_token: str) -> db_module.PostgrestClient:
        return fake.client_factory(jwt_token)

    monkeypatch.setattr(budgets_module, "user_client", _factory)
    return fake


def test_create_budget_requires_auth() -> None:
    response = client.post("/api/v1/budgets", json={"month": "2026-07", "cap_paise": 100000})

    assert response.status_code == 401


def test_create_overall_budget_hits_correct_on_conflict_target(fake_postgrest) -> None:
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07", "category": None, "cap_paise": 500000},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    assert response.json()["category"] is None
    request = fake_postgrest.requests[0]
    assert request.url.params["on_conflict"] == "user_id,month,category"
    body = json.loads(request.content)
    assert body == {
        "user_id": "user-123",
        "month": "2026-07-01",
        "category": "__overall__",
        "cap_paise": 500000,
    }


def test_create_category_budget_hits_correct_on_conflict_target(fake_postgrest) -> None:
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07", "category": "food", "cap_paise": 100000},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = json.loads(fake_postgrest.requests[0].content)
    assert body["category"] == "food"


def test_create_budget_normalizes_mid_month_date_before_db_call(fake_postgrest) -> None:
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07-15", "category": "food", "cap_paise": 100000},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = json.loads(fake_postgrest.requests[0].content)
    assert body["month"] == "2026-07-01"


def test_create_budget_rejects_reserved_sentinel_category(fake_postgrest) -> None:
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07", "category": "__overall__", "cap_paise": 100000},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert fake_postgrest.requests == []


def test_create_budget_rejects_non_positive_cap(fake_postgrest) -> None:
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07", "category": "food", "cap_paise": 0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert fake_postgrest.requests == []


def test_get_budgets_requires_auth() -> None:
    response = client.get("/api/v1/budgets", params={"month": "2026-07"})

    assert response.status_code == 401


def test_get_budgets_returns_progress_matching_hand_computed_expectation(fake_postgrest) -> None:
    now_iso = datetime.now(UTC).isoformat()
    fake_postgrest.fixtures = {
        "budgets": [
            {
                "id": "b-food",
                "user_id": "user-123",
                "month": "2026-07-01",
                "category": "food",
                "cap_paise": 10000,
            },
            {
                "id": "b-snacks",
                "user_id": "user-123",
                "month": "2026-07-01",
                "category": "snacks",
                "cap_paise": 50000,
            },
        ],
        "orders": [
            {
                "id": "o1",
                "user_id": "user-123",
                "platform": "swiggy_instamart",
                "is_cancelled": False,
                "ordered_at": now_iso,
                "grand_total_paise": 17000,
            }
        ],
        "order_items": [
            {"order_id": "o1", "category": "food", "quantity": 2, "unit_price_paise": 6000},
            {"order_id": "o1", "category": "snacks", "quantity": 1, "unit_price_paise": 5000},
        ],
    }
    token = _make_token()

    response = client.get(
        "/api/v1/budgets",
        params={"month": "2026-07"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["month"] == "2026-07-01"
    by_category = {row["category"]: row for row in body["budgets"]}

    assert by_category["food"]["id"] == "b-food"
    assert by_category["food"]["spent_paise"] == 12000
    assert by_category["food"]["pct"] == pytest.approx(1.2)
    assert by_category["food"]["status"] == "over"

    assert by_category["snacks"]["id"] == "b-snacks"
    assert by_category["snacks"]["spent_paise"] == 5000
    assert by_category["snacks"]["pct"] == pytest.approx(0.1)
    assert by_category["snacks"]["status"] == "ok"


def test_get_budgets_normalizes_month_before_db_call(fake_postgrest) -> None:
    fake_postgrest.fixtures = {"budgets": [], "orders": [], "order_items": []}
    token = _make_token()

    response = client.get(
        "/api/v1/budgets",
        params={"month": "2026-07"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    budgets_request = next(r for r in fake_postgrest.requests if r.url.path.endswith("/budgets"))
    assert budgets_request.url.params["month"] == "eq.2026-07-01"


def test_delete_budget_requires_auth() -> None:
    response = client.delete("/api/v1/budgets/b1")

    assert response.status_code == 401


def test_delete_budget_removes_only_budget_row(fake_postgrest) -> None:
    token = _make_token()

    response = client.delete("/api/v1/budgets/b1", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204
    assert response.content == b""
    assert len(fake_postgrest.requests) == 1
    request = fake_postgrest.requests[0]
    assert request.method == "DELETE"
    assert request.url.path.endswith("/budgets")
    assert request.url.params["id"] == "eq.b1"
