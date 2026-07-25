"""Unit tests for `GET /api/v1/recommendations/usuals` (#40, ADR-0004) and
`GET /api/v1/recommendations/suggestions` (#41, ADR-0004 §4).

Mocks `linked_accounts`, `engine.resolve_access_token`, and the adapter
`get_usual_items`/`search_products`/`search_menu` functions (their own
behaviour is covered by the adapter unit tests and
`test_mcp_recommendations.py`) — this file only asserts each route's own
contract: response shape, the Zepto join key (AC-2), Food grouping/ranking,
the no-account/no-orders empty-list paths, the usuals route never calling a
live search tool, and the suggestions route's qualification rule (cheaper
and/or lower-calorie, AC-3/AC-5).
"""

from __future__ import annotations

import inspect
import re
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
from app.mcp.adapters.swiggy_instamart import InstamartUsualItem
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


@pytest.fixture
def no_live_search_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A usuals request must never fan out to a live catalogue/menu search —
    only `get_usual_items` (pre-aggregated) may be called. Any accidental
    `search_products`/`search_menu` call fails the test loudly. Not autouse:
    the suggestions tests below need these functions to actually be called.
    """

    def _fail(*args, **kwargs):
        raise AssertionError("usuals must not call a live search tool")

    monkeypatch.setattr(recommendations_module.zepto, "search_products", _fail)
    monkeypatch.setattr(recommendations_module.swiggy_instamart, "search_products", _fail)
    monkeypatch.setattr(recommendations_module.swiggy_food, "search_menu", _fail)


client = TestClient(app)


def test_get_usuals_requires_auth() -> None:
    response = client.get("/api/v1/recommendations/usuals")

    assert response.status_code == 401


def test_get_usuals_returns_empty_lists_when_nothing_linked_or_synced(
    monkeypatch: pytest.MonkeyPatch, fake_user_client, no_live_search_calls
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts, "get_linked_account", lambda *a, **kw: None
    )
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(recommendations_module, "list_order_items_for_orders", lambda *a, **kw: [])
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/usuals", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"zepto": [], "swiggy_instamart": [], "swiggy_food": []}
    assert fake_user_client.closed is True


def test_zepto_usuals_use_product_variant_id_as_the_join_key(
    monkeypatch: pytest.MonkeyPatch, fake_user_client, no_live_search_calls
) -> None:
    """AC-2: Zepto usuals must key on `productVariantId`, not a derived name."""
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
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(recommendations_module, "list_order_items_for_orders", lambda *a, **kw: [])
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/usuals", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["zepto"] == [
        {
            "platform": "zepto",
            "key": "pv-42",
            "name": "Amul Milk",
            "frequency_rank_or_count": 1,
            "avg_unit_price_paise": 6000,
            "calorie_estimate": None,
            "redirect_url": "https://www.zeptonow.com/pn/amul-milk/pvid/pv-42",
        }
    ]


def test_instamart_usuals_key_on_item_id(
    monkeypatch: pytest.MonkeyPatch, fake_user_client, no_live_search_calls
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts,
        "get_linked_account",
        lambda client, *, user_id, platform: (
            {"tokens_encrypted": "enc"} if platform == "swiggy" else None
        ),
    )
    monkeypatch.setattr(
        recommendations_module.engine, "resolve_access_token", lambda *a, **kw: "at-1"
    )
    monkeypatch.setattr(
        recommendations_module.swiggy_instamart,
        "get_usual_items",
        lambda client, base_url, access_token: [
            InstamartUsualItem(
                item_id="item-9",
                name="Toned Milk",
                frequency_rank=1,
                unit_price_paise=5500,
                redirect_url="https://www.swiggy.com/instamart/item/item-9",
            )
        ],
    )
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(recommendations_module, "list_order_items_for_orders", lambda *a, **kw: [])
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/usuals", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["swiggy_instamart"] == [
        {
            "platform": "swiggy_instamart",
            "key": "item-9",
            "name": "Toned Milk",
            "frequency_rank_or_count": 1,
            "avg_unit_price_paise": 5500,
            "calorie_estimate": None,
            "redirect_url": "https://www.swiggy.com/instamart/item/item-9",
        }
    ]


def test_food_usuals_group_across_two_orders_with_the_same_normalized_name(
    monkeypatch: pytest.MonkeyPatch, fake_user_client, no_live_search_calls
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts, "get_linked_account", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        recommendations_module,
        "list_orders",
        lambda client, *, user_id, platform, is_cancelled: [{"id": "o1"}, {"id": "o2"}],
    )
    monkeypatch.setattr(
        recommendations_module,
        "list_order_items_for_orders",
        lambda client, *, order_ids: [
            {"order_id": "o1", "name": "Chicken Biryani", "unit_price_paise": 25000},
            {"order_id": "o2", "name": "  chicken biryani  ", "unit_price_paise": 27000},
        ],
    )
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/usuals", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["swiggy_food"] == [
        {
            "platform": "swiggy_food",
            "key": "chicken biryani",
            "name": "Chicken Biryani",
            "frequency_rank_or_count": 2,
            "avg_unit_price_paise": 26000,
            "calorie_estimate": None,
            "redirect_url": "https://www.swiggy.com/search?query=Chicken%20Biryani",
        }
    ]


def test_food_usuals_empty_when_no_synced_food_orders(
    monkeypatch: pytest.MonkeyPatch, fake_user_client, no_live_search_calls
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts, "get_linked_account", lambda *a, **kw: None
    )
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(
        recommendations_module, "list_order_items_for_orders", lambda client, *, order_ids: []
    )
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/usuals", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["swiggy_food"] == []


def test_recommendations_module_never_calls_call_tool_directly() -> None:
    """NFR-1: `api/recommendations.py` must never invoke `call_tool` with an
    explicit tool name itself — it only ever passes the function through to
    the read-only adapter wrappers (`get_usual_items`/`search_products`/
    `search_menu`), which keep tool-name knowledge inside `mcp/adapters/*`.
    """
    source = inspect.getsource(recommendations_module)
    assert re.search(r'call_tool\(\s*[^,]+,\s*[^,]+,\s*"', source) is None


def test_get_suggestions_requires_auth() -> None:
    response = client.get("/api/v1/recommendations/suggestions")

    assert response.status_code == 401


def test_get_suggestions_returns_empty_lists_when_nothing_linked_or_synced(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    monkeypatch.setattr(
        recommendations_module.linked_accounts, "get_linked_account", lambda *a, **kw: None
    )
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(recommendations_module, "list_order_items_for_orders", lambda *a, **kw: [])
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"zepto": [], "swiggy_instamart": [], "swiggy_food": []}
    assert fake_user_client.closed is True


def test_zepto_suggestion_returns_a_cheaper_live_candidate(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    """AC-3: a frequent item with a cheaper live candidate returns it."""
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
                name="Toned Milk 500ml",
                unit_price_paise=5500,
                redirect_url="https://www.zeptonow.com/pn/toned-milk-500ml/pvid/pv-99",
                raw={},
            )
        ],
    )

    def _fail_estimate(*args, **kwargs):
        raise AssertionError(
            "a grocery (Zepto) item must never be assigned a synthetic calorie estimate"
        )

    monkeypatch.setattr(recommendations_module.llm_calories, "estimate_calories", _fail_estimate)
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(recommendations_module, "list_order_items_for_orders", lambda *a, **kw: [])
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["zepto"] == [
        {
            "platform": "zepto",
            "key": "pv-42",
            "name": "Amul Milk",
            "avg_unit_price_paise": 6000,
            "calorie_estimate": None,
            "suggested_name": "Toned Milk 500ml",
            "suggested_unit_price_paise": 5500,
            "suggested_calorie_estimate": None,
            "suggested_redirect_url": "https://www.zeptonow.com/pn/toned-milk-500ml/pvid/pv-99",
            "cheaper": True,
            "lower_calorie": False,
        }
    ]


def test_zepto_suggestion_omitted_when_no_live_candidate_qualifies(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    """AC-5: a frequent item whose live search returns nothing better is
    omitted from the response entirely — not present, not a placeholder.
    """
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
                name="Amul Milk 1L Pack",
                unit_price_paise=6500,
                redirect_url="https://www.zeptonow.com/pn/amul-milk-1l-pack/pvid/pv-77",
                raw={},
            )
        ],
    )
    monkeypatch.setattr(recommendations_module, "list_orders", lambda *a, **kw: [])
    monkeypatch.setattr(recommendations_module, "list_order_items_for_orders", lambda *a, **kw: [])
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["zepto"] == []


def test_food_suggestion_qualifies_on_calorie_estimate_when_not_cheaper(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    """A ready-to-eat Food item may qualify on the calorie axis alone —
    the candidate here is pricier but the shared `estimate_calories`
    function (mocked) returns a lower kcal figure.
    """
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
        recommendations_module.swiggy_instamart, "get_usual_items", lambda *a, **kw: []
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
                "calorie_estimate": 650,
            }
        ],
    )
    monkeypatch.setattr(
        recommendations_module.swiggy_food,
        "search_menu",
        lambda client, base_url, access_token, query: [
            SearchResultItem(
                name="Veg Biryani",
                unit_price_paise=26000,
                redirect_url="https://www.swiggy.com/restaurants/some-place-rest1",
                raw={},
            )
        ],
    )
    monkeypatch.setattr(recommendations_module.llm_calories, "estimate_calories", lambda name: 500)
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["swiggy_food"] == [
        {
            "platform": "swiggy_food",
            "key": "chicken biryani",
            "name": "Chicken Biryani",
            "avg_unit_price_paise": 25000,
            "calorie_estimate": 650,
            "suggested_name": "Veg Biryani",
            "suggested_unit_price_paise": 26000,
            "suggested_calorie_estimate": 500,
            "suggested_redirect_url": "https://www.swiggy.com/restaurants/some-place-rest1",
            "cheaper": False,
            "lower_calorie": True,
        }
    ]


def test_food_suggestion_never_estimates_calories_when_frequent_item_has_none(
    monkeypatch: pytest.MonkeyPatch, fake_user_client
) -> None:
    """FR-6 excluded this item at ingest (no stored `calorie_estimate`); the
    comparison must stay price-only rather than estimating on the fly.
    """
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
        recommendations_module.swiggy_instamart, "get_usual_items", lambda *a, **kw: []
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
            {"order_id": "o1", "name": "Chicken Biryani", "unit_price_paise": 25000}
        ],
    )
    monkeypatch.setattr(
        recommendations_module.swiggy_food,
        "search_menu",
        lambda client, base_url, access_token, query: [
            SearchResultItem(
                name="Veg Biryani",
                unit_price_paise=26000,
                redirect_url="https://www.swiggy.com/restaurants/some-place-rest1",
                raw={},
            )
        ],
    )

    def _fail_estimate(*args, **kwargs):
        raise AssertionError("must not estimate calories when the frequent item has none")

    monkeypatch.setattr(recommendations_module.llm_calories, "estimate_calories", _fail_estimate)
    token = _make_token()

    response = client.get(
        "/api/v1/recommendations/suggestions", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["swiggy_food"] == []
