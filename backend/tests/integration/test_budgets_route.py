"""Integration test for the budget CRUD + progress routes (#69): asserts
`backend/app/api/budgets.py` wires `core/budgets.py` (#67) and
`analytics/aggregate.py`'s `budget_progress()` (#68) into
docs/architecture/features/6-budgeting.md §3's contract, against a mocked
`PostgrestClient`/fixture rows — never a live PostgREST/Supabase instance
(see backend/tests/integration/README.md).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

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


@dataclass
class _FakeClient:
    """Stands in for `PostgrestClient`. Distinguishes calls by table name;
    `budgets` upsert/delete calls are recorded distinctly from plain
    `select`s so tests can assert `DELETE` never touches `orders`/
    `order_items` and `POST` targets the right `on_conflict`.
    """

    budgets: list[dict]
    orders: list[dict]
    order_items: list[dict]
    calls: list[tuple[str, str, dict | None]] = field(default_factory=list)
    closed: bool = False
    upserted: list[dict] = field(default_factory=list)
    deleted_filters: list[dict] = field(default_factory=list)

    def select(self, table: str, *, columns: str = "*", filters: dict | None = None) -> list[dict]:
        self.calls.append(("select", table, filters))
        if table == "budgets":
            return self.budgets
        if table == "orders":
            return self.orders
        if table == "order_items":
            return self.order_items
        raise AssertionError(f"unexpected table queried: {table!r}")

    def upsert(self, table: str, rows: dict, *, on_conflict: str) -> list[dict]:
        self.calls.append(("upsert", table, {"on_conflict": on_conflict, **rows}))
        self.upserted.append(rows)
        return [
            {
                "id": "b-new",
                "user_id": rows["user_id"],
                "month": rows["month"],
                "category": rows["category"],
                "cap_paise": rows["cap_paise"],
            }
        ]

    def delete(self, table: str, *, filters: dict) -> list[dict]:
        self.calls.append(("delete", table, filters))
        self.deleted_filters.append(filters)
        return []

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_user_client(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, _FakeClient] = {}

    def _factory(token: str) -> _FakeClient:
        return state["fake"]

    def _install(
        *,
        budgets: list[dict] | None = None,
        orders: list[dict] | None = None,
        order_items: list[dict] | None = None,
    ) -> _FakeClient:
        fake = _FakeClient(
            budgets=budgets or [], orders=orders or [], order_items=order_items or []
        )
        state["fake"] = fake
        monkeypatch.setattr(budgets_module, "user_client", _factory)
        return fake

    return _install


client = TestClient(app)


def test_post_budgets_requires_auth() -> None:
    response = client.post("/api/v1/budgets", json={"month": "2026-07-01", "cap_paise": 100000})

    assert response.status_code == 401


def test_get_budgets_requires_auth() -> None:
    response = client.get("/api/v1/budgets", params={"month": "2026-07"})

    assert response.status_code == 401


def test_post_budgets_upserts_overall_cap(fake_user_client) -> None:
    fake = fake_user_client()
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07-01", "cap_paise": 500000},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["category"] is None
    assert fake.upserted == [
        {
            "user_id": "user-123",
            "month": "2026-07-01",
            "category": "__overall__",
            "cap_paise": 500000,
        }
    ]
    upsert_call = next(call for call in fake.calls if call[0] == "upsert")
    assert upsert_call[2]["on_conflict"] == "user_id,month,category"


def test_post_budgets_upserts_per_category_cap(fake_user_client) -> None:
    fake = fake_user_client()
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07-01", "category": "grocery", "cap_paise": 200000},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["category"] == "grocery"
    assert fake.upserted == [
        {
            "user_id": "user-123",
            "month": "2026-07-01",
            "category": "grocery",
            "cap_paise": 200000,
        }
    ]


def test_post_budgets_normalizes_mid_month_date_before_db_call(fake_user_client) -> None:
    fake = fake_user_client()
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07-15", "cap_paise": 100000},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert fake.upserted[0]["month"] == "2026-07-01"


def test_post_budgets_rejects_non_positive_cap(fake_user_client) -> None:
    fake_user_client()
    token = _make_token()

    response = client.post(
        "/api/v1/budgets",
        json={"month": "2026-07-01", "cap_paise": 0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


def test_get_budgets_returns_progress_for_over_and_under_cap_categories(fake_user_client) -> None:
    budget_rows = [
        {
            "id": "b1",
            "user_id": "user-123",
            "month": "2026-07-01",
            "category": "food",
            "cap_paise": 10000,
        },
        {
            "id": "b2",
            "user_id": "user-123",
            "month": "2026-07-01",
            "category": "grocery",
            "cap_paise": 100000,
        },
    ]
    orders = [
        {
            "id": "o1",
            "platform": "swiggy_food",
            "is_cancelled": False,
            "grand_total_paise": 15000,
            "ordered_at": "2026-07-10T09:00:00Z",
        },
        {
            "id": "o2",
            "platform": "swiggy_instamart",
            "is_cancelled": False,
            "grand_total_paise": 20000,
            "ordered_at": "2026-07-11T09:00:00Z",
        },
    ]
    order_items = [
        {
            "order_id": "o1",
            "name": "Thali",
            "quantity": 1,
            "unit_price_paise": 15000,
            "category": "food",
        },
        {
            "order_id": "o2",
            "name": "Milk",
            "quantity": 2,
            "unit_price_paise": 10000,
            "category": "grocery",
        },
    ]
    fake = fake_user_client(budgets=budget_rows, orders=orders, order_items=order_items)
    token = _make_token()

    response = client.get(
        "/api/v1/budgets",
        params={"month": "2026-07"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["month"] == "2026-07"
    assert body["budgets"] == [
        {
            "id": "b1",
            "category": "food",
            "cap_paise": 10000,
            "spent_paise": 15000,
            "pct": 1.5,
            "status": "over",
        },
        {
            "id": "b2",
            "category": "grocery",
            "cap_paise": 100000,
            "spent_paise": 20000,
            "pct": 0.2,
            "status": "ok",
        },
    ]
    assert fake.closed is True
    budgets_call = next(call for call in fake.calls if call[1] == "budgets")
    assert budgets_call[2] == {"user_id": "eq.user-123", "month": "eq.2026-07-01"}


def test_get_budgets_no_orders_returns_zero_spend(fake_user_client) -> None:
    budget_rows = [
        {
            "id": "b1",
            "user_id": "user-123",
            "month": "2026-07-01",
            "category": None,
            "cap_paise": 500000,
        }
    ]
    fake_user_client(budgets=budget_rows, orders=[], order_items=[])
    token = _make_token()

    response = client.get(
        "/api/v1/budgets",
        params={"month": "2026-07"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()["budgets"]
    assert body == [
        {
            "id": "b1",
            "category": None,
            "cap_paise": 500000,
            "spent_paise": 0,
            "pct": 0.0,
            "status": "ok",
        }
    ]


def test_delete_budget_only_touches_budgets_table(fake_user_client) -> None:
    fake = fake_user_client()
    token = _make_token()

    response = client.delete("/api/v1/budgets/b1", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204
    assert fake.deleted_filters == [{"id": "eq.b1"}]
    assert [call for call in fake.calls if call[1] in ("orders", "order_items")] == []
