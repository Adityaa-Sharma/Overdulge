import json

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.digest import send


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
    settings = Settings(
        supabase_url="https://project.supabase.co",
        supabase_service_role_key="service-role-key",
        resend_api_key="resend-key",
        digest_from_email="digest@overdulge.example",
    )
    monkeypatch.setattr(send, "get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


def test_resolve_user_email_calls_gotrue_admin_endpoint_with_service_role_key():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "user-1", "email": "user@example.com"})

    email = send.resolve_user_email("user-1", transport=httpx.MockTransport(handler))

    assert email == "user@example.com"
    request = captured[0]
    assert request.url.path == "/auth/v1/admin/users/user-1"
    assert request.headers["apikey"] == "service-role-key"
    assert request.headers["authorization"] == "Bearer service-role-key"


def test_resolve_user_email_returns_none_when_no_email_on_record():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "user-1"})

    email = send.resolve_user_email("user-1", transport=httpx.MockTransport(handler))

    assert email is None


def test_resolve_user_email_raises_on_admin_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(send.DigestSendError):
        send.resolve_user_email("user-1", transport=httpx.MockTransport(handler))


def test_send_digest_email_posts_correct_resend_payload():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "email-1"})

    send.send_digest_email(
        to_email="user@example.com",
        subject="Your weekly Overdulge digest",
        html="<html><body>hi</body></html>",
        transport=httpx.MockTransport(handler),
    )

    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer resend-key"
    body = json.loads(request.content)
    assert body == {
        "from": "digest@overdulge.example",
        "to": ["user@example.com"],
        "subject": "Your weekly Overdulge digest",
        "html": "<html><body>hi</body></html>",
    }


def test_send_digest_email_raises_on_resend_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="invalid payload")

    with pytest.raises(send.DigestSendError):
        send.send_digest_email(
            to_email="user@example.com",
            subject="subject",
            html="<html></html>",
            transport=httpx.MockTransport(handler),
        )


def test_send_digest_email_requires_resend_credentials(monkeypatch):
    monkeypatch.setattr(
        send,
        "get_settings",
        lambda: Settings(supabase_url="https://project.supabase.co"),
    )

    with pytest.raises(RuntimeError):
        send.send_digest_email(to_email="user@example.com", subject="s", html="<html></html>")
