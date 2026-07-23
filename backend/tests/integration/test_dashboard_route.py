"""Integration test for `GET /api/v1/dashboard` (#61): asserts the route
wires `analytics/aggregate.py` (#60) into the exact response contract from
docs/architecture/features/4-spend-dashboard.md §3, against a mocked
`PostgrestClient`/fixture rows — never a live PostgREST/Supabase instance
(see backend/tests/integration/README.md).
"""

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
from app.api import dashboard as dashboard_module
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
    """Stands in for `PostgrestClient`: distinguishes the route's three calls
    by their `filters`/`columns` shape (§3: the unfiltered `has_data` count,
    the `is_cancelled=eq.false` orders fetch, and the `order_items` fetch),
    records every call so tests can assert on it, and tracks `close()` like
    the real client's lifecycle.
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
        if table == "orders" and filters is None:
            return self._all_orders
        if table == "orders":
            return self._orders
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
        monkeypatch.setattr(dashboard_module, "user_client", _factory)
        return fake

    return _install


client = TestClient(app)


def test_get_dashboard_requires_auth() -> None:
    response = client.get("/api/v1/dashboard")

    assert response.status_code == 401


def test_get_dashboard_zero_orders_returns_has_data_false_with_empty_shape(fake_user_client):
    fake_user_client(all_orders=[], orders=[], order_items=[])
    token = _make_token()

    response = client.get("/api/v1/dashboard", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is False
    assert body["totals"]["this_week_paise"] == {
        "combined": 0,
        "swiggy_food": 0,
        "swiggy_instamart": 0,
        "zepto": 0,
    }
    assert body["top_restaurants"] == []
    assert body["top_products"] == []
    assert body["location_lens"] == []
    assert body["category_breakdown"] == {
        "food_delivery_paise": 0,
        "grocery_paise": 0,
        "item_categories_paise": {},
    }
    assert body["order_stats"]["swiggy_food"] == {"order_count": 0, "avg_order_value_paise": None}
    assert body["projection"]["label"] == "Projection"


def test_get_dashboard_cancelled_orders_only_returns_has_data_true_with_zero_totals(
    fake_user_client,
):
    fake_user_client(
        all_orders=[{"id": "o1"}],  # unfiltered count: one order exists (cancelled)
        orders=[],  # is_cancelled=eq.false excludes it
        order_items=[],
    )
    token = _make_token()

    response = client.get("/api/v1/dashboard", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert body["totals"]["this_month_paise"]["combined"] == 0
    assert body["top_restaurants"] == []


def test_get_dashboard_populated_fixture_matches_response_contract(fake_user_client):
    orders = [
        {
            "id": "o1",
            "platform": "swiggy_food",
            "vendor_name": "Tasty Bites",
            "address_id": "addr-1",
            "is_cancelled": False,
            "grand_total_paise": 50000,
            "ordered_at": "2026-07-22T09:00:00Z",
        },
        {
            "id": "o2",
            "platform": "swiggy_instamart",
            "vendor_name": None,
            "address_id": None,
            "is_cancelled": False,
            "grand_total_paise": 30000,
            "ordered_at": "2026-07-21T09:00:00Z",
        },
    ]
    order_items = [
        {
            "order_id": "o2",
            "name": "Milk",
            "quantity": 2,
            "unit_price_paise": 15000,
            "category": "grocery",
        }
    ]
    fake = fake_user_client(orders=orders, order_items=order_items)
    token = _make_token()

    response = client.get("/api/v1/dashboard", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert body["totals"]["this_month_paise"]["combined"] == 80000
    assert body["top_restaurants"] == [
        {"name": "Tasty Bites", "spend_paise": 50000, "order_count": 1}
    ]
    assert body["top_products"] == [{"name": "Milk", "spend_paise": 30000, "order_count": 1}]
    assert body["location_lens"] == [
        {"address_id": "addr-1", "spend_paise": 50000, "order_count": 1}
    ]
    assert body["order_stats"]["swiggy_food"] == {
        "order_count": 1,
        "avg_order_value_paise": 50000,
    }
    assert body["projection"]["label"] == "Projection"
    assert body["generated_at"].endswith("Z")
    assert fake.closed is True

    order_calls = [call for call in fake.calls if call[0] == "orders"]
    assert len(order_calls) == 2
    assert {"is_cancelled": "eq.false"} in [call[1] for call in order_calls]
    item_calls = [call for call in fake.calls if call[0] == "order_items"]
    assert item_calls == [("order_items", {"order_id": "in.(o1,o2)"})]
