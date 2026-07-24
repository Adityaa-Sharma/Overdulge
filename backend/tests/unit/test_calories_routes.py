"""Unit tests for `GET /api/v1/calories` and `GET /api/v1/calories/commentary`
(#82; docs/architecture/features/7-calorie-estimation.md §6, ADR-0008 §5).

`/calories` is asserted against a mocked `PostgrestClient`/fixture rows, same
convention as `test_dashboard_route.py` — never a live PostgREST instance.
`/calories/commentary` mocks `llm.calories.generate_weekly_blurb` itself;
that function's own guard behaviour is covered by `test_calories.py`.
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
from app.api import calories as calories_module
from app.llm import calories as calories_module_llm
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
    """Distinguishes the route's `orders` calls by whether `is_cancelled` is
    in the filter (the unfiltered `has_data` count vs. the
    `is_cancelled=eq.false` fetch), same convention as
    `test_dashboard_route.py`.
    """

    def __init__(
        self, *, all_orders: list[dict], orders: list[dict], order_items: list[dict]
    ) -> None:
        self._all_orders = all_orders
        self._orders = orders
        self._order_items = order_items
        self.calls: list[tuple[str, dict | None]] = []
        self.closed = False

    def select(self, table: str, *, columns: str = "*", filters: dict | None = None) -> list[dict]:
        self.calls.append((table, filters))
        if table == "orders":
            if filters and "is_cancelled" in filters:
                return self._orders
            return self._all_orders
        if table == "order_items":
            return self._order_items
        raise AssertionError(f"unexpected table queried: {table!r}")

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_user_client(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, _FakeClient] = {}

    def _factory(token: str) -> _FakeClient:
        return state["fake"]

    def _install(
        *,
        all_orders: list[dict] | None = None,
        orders: list[dict] | None = None,
        order_items: list[dict] | None = None,
    ) -> _FakeClient:
        fake = _FakeClient(
            all_orders=all_orders if all_orders is not None else (orders or []),
            orders=orders or [],
            order_items=order_items or [],
        )
        state["fake"] = fake
        monkeypatch.setattr(calories_module, "user_client", _factory)
        return fake

    return _install


client = TestClient(app)


def test_get_calories_requires_auth() -> None:
    response = client.get("/api/v1/calories")

    assert response.status_code == 401


def test_get_calories_zero_orders_returns_has_data_false_with_zeroed_shape(fake_user_client):
    fake_user_client(all_orders=[], orders=[], order_items=[])
    token = _make_token()

    response = client.get("/api/v1/calories", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is False
    assert body["totals"] == {"this_week_estimate_kcal": 0, "this_month_estimate_kcal": 0}
    assert body["trend"]["weekly"][-1]["estimate_kcal"] == 0
    assert body["generated_at"].endswith("Z")


def test_get_calories_cancelled_orders_only_returns_has_data_true_with_zero_totals(
    fake_user_client,
):
    fake_user_client(
        all_orders=[{"id": "o1"}],  # unfiltered count: one order exists (cancelled)
        orders=[],  # is_cancelled=eq.false excludes it
        order_items=[],
    )
    token = _make_token()

    response = client.get("/api/v1/calories", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert body["totals"]["this_week_estimate_kcal"] == 0


def test_get_calories_populated_fixture_matches_response_contract(fake_user_client):
    now = datetime.now(UTC)
    orders = [
        {
            "id": "o1",
            "platform": "swiggy_food",
            "is_cancelled": False,
            "grand_total_paise": 50000,
            "ordered_at": now.isoformat().replace("+00:00", "Z"),
        }
    ]
    order_items = [
        {"order_id": "o1", "name": "Masala dosa", "category": None, "calorie_estimate": 350}
    ]
    fake = fake_user_client(orders=orders, order_items=order_items)
    token = _make_token()

    response = client.get("/api/v1/calories", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert body["totals"]["this_week_estimate_kcal"] == 350
    assert body["totals"]["this_month_estimate_kcal"] == 350
    assert body["trend"]["weekly"][-1]["estimate_kcal"] == 350
    assert fake.closed is True

    order_calls = [call for call in fake.calls if call[0] == "orders"]
    assert len(order_calls) == 2
    assert any((call[1] or {}).get("is_cancelled") == "eq.false" for call in order_calls)


def test_get_calories_commentary_requires_auth() -> None:
    response = client.get("/api/v1/calories/commentary")

    assert response.status_code == 401


def test_get_calories_commentary_returns_generated_blurb(fake_user_client, monkeypatch):
    now = datetime.now(UTC)
    this_week_order = {
        "id": "o1",
        "platform": "swiggy_food",
        "is_cancelled": False,
        "grand_total_paise": 50000,
        "ordered_at": now.isoformat().replace("+00:00", "Z"),
    }
    last_week_order = {
        "id": "o2",
        "platform": "swiggy_food",
        "is_cancelled": False,
        "grand_total_paise": 20000,
        "ordered_at": (now - timedelta(days=10)).isoformat().replace("+00:00", "Z"),
    }
    order_items = [
        {"order_id": "o1", "name": "Masala dosa", "category": None, "calorie_estimate": 350}
    ]
    fake_user_client(orders=[this_week_order, last_week_order], order_items=order_items)

    captured: dict = {}

    def fake_generate_weekly_blurb(
        *, week_kcal_estimate: int, week_order_count: int, week_spend_paise: int
    ) -> str:
        captured["week_kcal_estimate"] = week_kcal_estimate
        captured["week_order_count"] = week_order_count
        captured["week_spend_paise"] = week_spend_paise
        return "Twelve orders and counting — your wallet's on a streak."

    monkeypatch.setattr(
        calories_module.calories, "generate_weekly_blurb", fake_generate_weekly_blurb
    )
    token = _make_token()

    response = client.get(
        "/api/v1/calories/commentary", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"blurb": "Twelve orders and counting — your wallet's on a streak."}
    assert captured == {
        "week_kcal_estimate": 350,
        "week_order_count": 1,
        "week_spend_paise": 50000,
    }


def test_get_calories_commentary_never_errors_into_broken_state_on_guard_fallback(
    fake_user_client, monkeypatch
):
    now = datetime.now(UTC)
    order = {
        "id": "o1",
        "platform": "swiggy_food",
        "is_cancelled": False,
        "grand_total_paise": 50000,
        "ordered_at": now.isoformat().replace("+00:00", "Z"),
    }
    fake_user_client(orders=[order], order_items=[])
    monkeypatch.setattr(
        calories_module.calories,
        "generate_weekly_blurb",
        lambda **kwargs: calories_module_llm.tone_guard.FALLBACK_BLURB,
    )
    token = _make_token()

    response = client.get(
        "/api/v1/calories/commentary", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"blurb": calories_module_llm.tone_guard.FALLBACK_BLURB}
