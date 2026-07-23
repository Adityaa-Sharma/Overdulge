from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.core import db
from app.core.config import Settings, get_settings
from app.digest import cron

# Matches backend/tests/unit/test_digest_render.py's fixed "now".
_NOW = datetime(2026, 7, 23, tzinfo=UTC)
_MONTH = "2026-07-01"


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch):
    settings = Settings(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        supabase_service_role_key="service-role-key",
        resend_api_key="resend-key",
        digest_from_email="digest@overdulge.example",
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    monkeypatch.setattr("app.digest.send.get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


class FakeBackend:
    """Single MockTransport handler standing in for PostgREST, GoTrue Admin,
    and Resend — routed by path/host, matching the one-`transport`-threaded-
    through convention `digest/cron.py` uses for all three calls.
    """

    def __init__(
        self,
        *,
        budgets: list[dict],
        orders: list[dict] | None = None,
        order_items: list[dict] | None = None,
        emails: dict[str, str] | None = None,
    ) -> None:
        self.tables = {
            "budgets": budgets,
            "orders": orders or [],
            "order_items": order_items or [],
        }
        self.emails = emails or {}
        self.sent_emails: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/rest/v1/"):
            table = path.rsplit("/", 1)[-1]
            rows = _filter_rows(self.tables.get(table, []), request.url.params)
            return httpx.Response(200, json=rows)
        if path.startswith("/auth/v1/admin/users/"):
            user_id = path.rsplit("/", 1)[-1]
            body: dict = {"id": user_id}
            if user_id in self.emails:
                body["email"] = self.emails[user_id]
            return httpx.Response(200, json=body)
        if request.url.host == "api.resend.com":
            payload = json.loads(request.content)
            self.sent_emails.append(payload)
            return httpx.Response(200, json={"id": f"email-{len(self.sent_emails)}"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _filter_rows(rows: list[dict], params: httpx.QueryParams) -> list[dict]:
    result = []
    for row in rows:
        matches = True
        for key, value in params.items():
            if key in ("select", "on_conflict"):
                continue
            actual = row.get(key)
            if value.startswith("eq."):
                target = value[len("eq.") :]
                actual_str = str(actual).lower() if isinstance(actual, bool) else str(actual)
                if actual_str != target:
                    matches = False
                    break
            elif value.startswith("in.("):
                ids = value[len("in.(") : -1].split(",") if value != "in.()" else []
                if str(actual) not in ids:
                    matches = False
                    break
        if matches:
            result.append(row)
    return result


def test_enumerates_only_users_with_a_current_month_budget_row():
    backend = FakeBackend(
        budgets=[
            {
                "id": "b1",
                "user_id": "user-a",
                "month": _MONTH,
                "category": "__overall__",
                "cap_paise": 500000,
            },
            {
                "id": "b2",
                "user_id": "user-a",
                "month": "2026-06-01",  # last month — not enumerated
                "category": "__overall__",
                "cap_paise": 500000,
            },
        ],
        emails={"user-a": "a@example.com"},
    )

    results = cron.run_weekly_digest(transport=httpx.MockTransport(backend.handler), now=_NOW)

    assert [r.user_id for r in results] == ["user-a"]
    assert results[0].success is True
    assert len(backend.sent_emails) == 1


def test_user_with_zero_current_month_budget_rows_receives_no_email():
    backend = FakeBackend(
        budgets=[
            {
                "id": "b1",
                "user_id": "user-with-cap",
                "month": _MONTH,
                "category": "__overall__",
                "cap_paise": 500000,
            }
        ],
        orders=[
            {
                "id": "o1",
                "user_id": "user-without-cap",
                "platform": "zepto",
                "is_cancelled": False,
                "ordered_at": "2026-07-19T12:00:00+00:00",
                "grand_total_paise": 5000,
            }
        ],
        emails={"user-with-cap": "cap@example.com", "user-without-cap": "nocap@example.com"},
    )

    results = cron.run_weekly_digest(transport=httpx.MockTransport(backend.handler), now=_NOW)

    assert [r.user_id for r in results] == ["user-with-cap"]
    assert len(backend.sent_emails) == 1
    assert backend.sent_emails[0]["to"] == ["cap@example.com"]


def test_digest_email_contains_rendered_spend_and_progress_content():
    backend = FakeBackend(
        budgets=[
            {
                "id": "b1",
                "user_id": "user-a",
                "month": _MONTH,
                "category": "food",
                "cap_paise": 10000,
            }
        ],
        orders=[
            {
                "id": "o1",
                "user_id": "user-a",
                "platform": "swiggy_instamart",
                "is_cancelled": False,
                "ordered_at": "2026-07-22T12:00:00+00:00",
                "grand_total_paise": 12000,
            }
        ],
        order_items=[
            {"order_id": "o1", "category": "food", "quantity": 2, "unit_price_paise": 6000}
        ],
        emails={"user-a": "a@example.com"},
    )

    cron.run_weekly_digest(transport=httpx.MockTransport(backend.handler), now=_NOW)

    assert len(backend.sent_emails) == 1
    sent = backend.sent_emails[0]
    assert sent["from"] == "digest@overdulge.example"
    assert sent["subject"] == "Your weekly Overdulge digest"
    assert "Over cap" in sent["html"]
    assert "<td>food</td>" in sent["html"]


def test_one_users_missing_email_does_not_stop_the_run_for_the_rest():
    backend = FakeBackend(
        budgets=[
            {
                "id": "b1",
                "user_id": "user-no-email",
                "month": _MONTH,
                "category": "__overall__",
                "cap_paise": 500000,
            },
            {
                "id": "b2",
                "user_id": "user-good",
                "month": _MONTH,
                "category": "__overall__",
                "cap_paise": 500000,
            },
        ],
        emails={"user-good": "good@example.com"},
    )

    results = cron.run_weekly_digest(transport=httpx.MockTransport(backend.handler), now=_NOW)

    by_user = {r.user_id: r for r in results}
    assert by_user["user-no-email"].success is False
    assert by_user["user-no-email"].error is not None
    assert by_user["user-good"].success is True
    assert len(backend.sent_emails) == 1
    assert backend.sent_emails[0]["to"] == ["good@example.com"]
