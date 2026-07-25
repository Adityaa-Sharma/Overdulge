"""Unit tests for `GET /api/v1/recommendations/suggestions` (#41, ADR-0004 §4).

Mocks `linked_accounts`, `engine.resolve_access_token`, the adapter
`get_usual_items`/`search_products`/`search_menu` functions, and
`llm.calories.estimate_calories`/`is_calorie_eligible` — this file only
asserts the route's own contract: a cheaper candidate is picked (AC-3), a
frequent item with no calorie estimate is never sent through
`estimate_calories` (ADR-0004 §4), a lower-calorie candidate is picked when
price alone doesn't qualify, and an item with no qualifying candidate is
omitted entirely rather than shown as a placeholder (AC-5).
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
from app.api import recommendations as recommendations_module
from app.main import app
from app.mcp.adapters.zepto import ZeptoUsualItem
from app.mcp.recommendations import SearchResultItem

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
    fake = _FakeClient()
    monkeypatch.setattr(recommendations_module, "user_client", lambda token: fake)
    return fake


@pytest.fixture(autouse=True)
def no_account_linked_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing linked and nothing synced unless a test opts in — keeps every
    test's setup limited to the platform branch it actually exercises."""
    monkeypatch.setattr(
        recommendations_module.linked_accounts, "get_linked_account", lambda *a, **kw: None
    )
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(recommendations_module, "list_order_items_for_orders", lambda *a, **kw: [])


@pytest.fixture(autouse=True)
def no_calorie_calls_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`estimate_calories`/`is_calorie_eligible` fail loudly unless a test
    explicitly stubs them — catches an accidental synthetic-calorie call for
    a grocery item that never had its own estimate (ADR-0004 §4)."""

    def _fail(*args, **kwargs):
        raise AssertionError("calorie estimation must not run for this scenario")

    monkeypatch.setattr(recommendations_module, "estimate_calories", _fail)
    monkeypatch.setattr(recommendations_module, "is_calorie_eligible", _fail)


client = TestClient(app)


def test_get_suggestions_requires_auth() -> None:
    response = client.get("/api/v1/recommendations/suggestions")

    assert response.status_code == 401


def test_returns_empty_suggestions_when_nothing_linked(
    fake_user_client,
) -> None:
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"suggestions": []}
    assert fake_user_client.closed is True


def test_zepto_item_with_a_cheaper_candidate_is_suggested(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: (
            {"tokens_encrypted": "enc"} if platform == "zepto" else None
        ),
    )
    monkeypatch.setattr(
        recommendations_module.engine, "resolve_access_token", lambda *a, **kw: "at-1"
    )
    monkeypatch.setattr(
        recommendations_module.zepto,
        "get_usual_items",
        lambda client, base_url, access_token: [
            ZeptoUsualItem(
                product_variant_id="pv-42",
                name="Amul Milk",
                frequency_rank=1,
                unit_price_paise=6000,
                redirect_url="https://www.zeptonow.com/pn/amul-milk/pvid/pv-42",
            )
        ],
    )
    monkeypatch.setattr(
        recommendations_module.zepto,
        "search_products",
        lambda client, base_url, access_token, query: [
            SearchResultItem(
                name="Amul Milk 500ml",
                unit_price_paise=5000,
                redirect_url="https://www.zeptonow.com/pn/amul-milk-500ml/pvid/pv-99",
                raw={"productVariantId": "pv-99"},
            )
        ],
    )
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "suggestions": [
            {
                "platform": "zepto",
                "frequent_item": {
                    "key": "pv-42",
                    "name": "Amul Milk",
                    "avg_unit_price_paise": 6000,
                    "calorie_estimate": None,
                },
                "alternative": {
                    "name": "Amul Milk 500ml",
                    "unit_price_paise": 5000,
                    "calorie_estimate": None,
                    "redirect_url": "https://www.zeptonow.com/pn/amul-milk-500ml/pvid/pv-99",
                    "cheaper": True,
                    "lower_calorie": False,
                },
            }
        ]
    }


def test_grocery_item_with_no_calorie_estimate_is_never_sent_to_estimate_calories(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    """Zepto/Instamart frequent items never carry a calorie estimate
    (ADR-0004 §4) — a costlier, non-cheaper candidate must be skipped on
    price alone, never trigger a synthetic calorie lookup."""
    monkeypatch.setattr(
        recommendations_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: (
            {"tokens_encrypted": "enc"} if platform == "zepto" else None
        ),
    )
    monkeypatch.setattr(
        recommendations_module.engine, "resolve_access_token", lambda *a, **kw: "at-1"
    )
    monkeypatch.setattr(
        recommendations_module.zepto,
        "get_usual_items",
        lambda client, base_url, access_token: [
            ZeptoUsualItem(
                product_variant_id="pv-42",
                name="Rice 5kg",
                frequency_rank=1,
                unit_price_paise=40000,
                redirect_url="https://www.zeptonow.com/pn/rice-5kg/pvid/pv-42",
            )
        ],
    )
    monkeypatch.setattr(
        recommendations_module.zepto,
        "search_products",
        lambda client, base_url, access_token, query: [
            SearchResultItem(
                name="Rice 5kg Premium",
                unit_price_paise=42000,
                redirect_url="https://www.zeptonow.com/pn/rice-5kg-premium/pvid/pv-99",
                raw={"category": "grocery"},
            )
        ],
    )
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"suggestions": []}


def test_item_with_no_qualifying_candidate_is_omitted_not_placeholder(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: (
            {"tokens_encrypted": "enc"} if platform == "zepto" else None
        ),
    )
    monkeypatch.setattr(
        recommendations_module.engine, "resolve_access_token", lambda *a, **kw: "at-1"
    )
    monkeypatch.setattr(
        recommendations_module.zepto,
        "get_usual_items",
        lambda client, base_url, access_token: [
            ZeptoUsualItem(
                product_variant_id="pv-42",
                name="Amul Milk",
                frequency_rank=1,
                unit_price_paise=6000,
                redirect_url="https://www.zeptonow.com/pn/amul-milk/pvid/pv-42",
            )
        ],
    )
    monkeypatch.setattr(
        recommendations_module.zepto,
        "search_products",
        lambda client, base_url, access_token, query: [],
    )
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"suggestions": []}
    assert "suggestions" in body and len(body["suggestions"]) == 0


def test_food_item_with_a_lower_calorie_candidate_is_suggested(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: (
            {"tokens_encrypted": "enc"} if platform == "swiggy" else None
        ),
    )
    monkeypatch.setattr(
        recommendations_module.engine, "resolve_access_token", lambda *a, **kw: "at-2"
    )
    monkeypatch.setattr(
        recommendations_module,
        "list_orders",
        lambda client, *, user_id, platform, is_cancelled: [{"id": "o1"}],
    )
    monkeypatch.setattr(
        recommendations_module,
        "list_order_items_for_orders",
        lambda client, *, order_ids: [
            {
                "order_id": "o1",
                "name": "Chicken Biryani",
                "unit_price_paise": 25000,
                "calorie_estimate": 600,
            }
        ],
    )
    monkeypatch.setattr(
        recommendations_module.swiggy_instamart, "get_usual_items", lambda *a, **kw: []
    )
    monkeypatch.setattr(
        recommendations_module.swiggy_food,
        "search_menu",
        lambda client, base_url, access_token, query: [
            SearchResultItem(
                name="Veg Biryani",
                unit_price_paise=27000,
                redirect_url="https://www.swiggy.com/restaurants/veg-place-rest1",
                raw={},
            )
        ],
    )
    monkeypatch.setattr(recommendations_module, "is_calorie_eligible", lambda *a, **kw: True)
    monkeypatch.setattr(recommendations_module, "estimate_calories", lambda name: 400)
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "suggestions": [
            {
                "platform": "swiggy_food",
                "frequent_item": {
                    "key": "chicken biryani",
                    "name": "Chicken Biryani",
                    "avg_unit_price_paise": 25000,
                    "calorie_estimate": 600,
                },
                "alternative": {
                    "name": "Veg Biryani",
                    "unit_price_paise": 27000,
                    "calorie_estimate": 400,
                    "redirect_url": "https://www.swiggy.com/restaurants/veg-place-rest1",
                    "cheaper": False,
                    "lower_calorie": True,
                },
            }
        ]
    }
