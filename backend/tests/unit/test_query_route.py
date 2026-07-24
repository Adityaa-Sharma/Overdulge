"""Unit tests for `POST /api/v1/query` (#37, ADR-0003).

Mocks `app.llm.agent.answer_query` — the agent's own tool-calling/grounding
behaviour is covered by `test_llm_agent.py`. This file only asserts the
route's own contract: auth is enforced before the handler runs, the
response shape for a successful answer, and that a timeout/agent-layer
failure returns the `{"error": {"code", "message"}}` envelope rather than a
bare 500.
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
from app.api import query as query_module
from app.core.config import get_settings
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


@pytest.fixture(autouse=True)
def fast_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the timeout test fast without waiting out the real 9s default."""
    settings = get_settings()
    monkeypatch.setattr(settings, "query_timeout_seconds", 0.05)


client = TestClient(app)


def test_ask_query_requires_auth() -> None:
    response = client.post("/api/v1/query", json={"question": "how much on milk?"})

    assert response.status_code == 401


def test_ask_query_rejects_empty_question() -> None:
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": ""},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


def test_ask_query_returns_agent_answer_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_answer_query(jwt_token: str, question: str) -> dict:
        captured["jwt"] = jwt_token
        captured["question"] = question
        return {"amount_paise": 12345, "explanation": "You spent ₹123.45.", "has_data": True}

    monkeypatch.setattr(query_module.agent, "answer_query", fake_answer_query)
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": "how much did I spend on milk since May?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "amount_paise": 12345,
        "explanation": "You spent ₹123.45.",
        "has_data": True,
    }
    assert captured["jwt"] == token
    assert captured["question"] == "how much did I spend on milk since May?"


def test_ask_query_not_enough_data_is_a_normal_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        query_module.agent,
        "answer_query",
        lambda jwt_token, question: {
            "amount_paise": None,
            "explanation": "I don't have enough data to answer that.",
            "has_data": False,
        },
    )
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": "how much did I spend on gold bars?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["has_data"] is False


def test_ask_query_timeout_returns_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_answer_query(jwt_token: str, question: str) -> dict:
        time.sleep(1)
        return {"amount_paise": 0, "explanation": "too slow", "has_data": True}

    monkeypatch.setattr(query_module.agent, "answer_query", slow_answer_query)
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": "how much did I spend on milk?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 504
    assert response.json() == {
        "error": {
            "code": "timeout",
            "message": "That question is taking too long to answer — please try again.",
        }
    }


def test_ask_query_agent_error_returns_error_envelope_not_a_stack_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_answer_query(jwt_token: str, question: str) -> dict:
        raise RuntimeError("upstream LLM provider blew up")

    monkeypatch.setattr(query_module.agent, "answer_query", broken_answer_query)
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": "how much did I spend on milk?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 502
    body = response.json()
    assert body == {
        "error": {
            "code": "agent_error",
            "message": "Something went wrong answering that question — please try again.",
        }
    }
    assert "upstream LLM provider blew up" not in response.text
