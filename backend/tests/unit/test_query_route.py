"""Unit tests for `POST /api/v1/query` (#37, ADR-0002/ADR-0003).

`llm/agent.py`'s `answer_query` is mocked — its own tool-calling/grounding
behaviour is covered by `test_llm_agent.py`. This file only asserts the
route's own contract: 401 before the handler runs, the expected JSON shape
on a mocked-agent success, and that a timeout/agent error returns the
`{"error": {"code", "message"}}` envelope rather than a bare 500.
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


def test_query_requires_auth() -> None:
    response = client.post("/api/v1/query", json={"question": "how much did I spend on milk?"})

    assert response.status_code == 401


def test_query_returns_agent_answer_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str]] = []

    def fake_answer_query(jwt_value: str, question: str) -> dict:
        captured.append((jwt_value, question))
        return {"amount_paise": 123456, "explanation": "You spent ₹1,234.56.", "has_data": True}

    monkeypatch.setattr(query_module.agent, "answer_query", fake_answer_query)
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": "how much did I spend on milk since May?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "amount_paise": 123456,
        "explanation": "You spent ₹1,234.56.",
        "has_data": True,
    }
    assert captured == [(token, "how much did I spend on milk since May?")]


def test_query_agent_timeout_returns_error_envelope_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_answer_query(jwt_value: str, question: str) -> dict:
        time.sleep(0.05)
        return {"amount_paise": None, "explanation": "too slow", "has_data": False}

    monkeypatch.setattr(query_module.agent, "answer_query", fake_answer_query)
    monkeypatch.setattr(query_module, "_QUERY_TIMEOUT_SECONDS", 0.01)
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": "how much did I spend?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 504
    body = response.json()
    assert body["error"]["code"] == "timeout"
    assert isinstance(body["error"]["message"], str)


def test_query_agent_error_returns_error_envelope_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_answer_query(jwt_value: str, question: str) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(query_module.agent, "answer_query", fake_answer_query)
    token = _make_token()

    response = client.post(
        "/api/v1/query",
        json={"question": "how much did I spend?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "agent_error"
    assert isinstance(body["error"]["message"], str)
