from datetime import UTC, datetime

from app.digest.render import render_digest_html

# Thursday — week_start lands on Monday 2026-07-20.
_NOW = datetime(2026, 7, 23, tzinfo=UTC)


def test_render_includes_spend_summary_totals():
    orders = [
        {
            "id": "o1",
            "platform": "swiggy_food",
            "ordered_at": "2026-07-22T12:00:00+00:00",  # this week + this month
            "grand_total_paise": 10000,
        },
        {
            "id": "o2",
            "platform": "zepto",
            "ordered_at": "2026-07-05T12:00:00+00:00",  # this month only
            "grand_total_paise": 15000,
        },
        {
            "id": "o3",
            "platform": "zepto",
            "ordered_at": "2026-06-01T12:00:00+00:00",  # neither
            "grand_total_paise": 999999,
        },
    ]

    html = render_digest_html(orders, [], [], now=_NOW)

    assert "This week: ₹100.00" in html
    assert "Month to date: ₹250.00" in html
    assert "9,999.99" not in html


def test_render_lists_progress_for_every_budget_row():
    orders = [
        {
            "id": "o1",
            "platform": "swiggy_instamart",
            "ordered_at": "2026-07-19T12:00:00+00:00",
            "grand_total_paise": 12000,
        }
    ]
    order_items = [{"order_id": "o1", "category": "food", "quantity": 2, "unit_price_paise": 6000}]
    budgets = [
        {"category": "food", "cap_paise": 10000},
        {"category": None, "cap_paise": 500000},
    ]

    html = render_digest_html(orders, order_items, budgets, now=_NOW)

    assert "<td>food</td>" in html
    assert "Over cap" in html
    assert "<td>Overall</td>" in html
    assert "On track" in html


def test_render_shows_empty_state_when_no_budgets_set():
    html = render_digest_html([], [], [], now=_NOW)

    assert "No budget caps set this month." in html
    assert "<table>" not in html


def test_render_is_pure_and_deterministic_for_same_inputs():
    orders = [
        {
            "id": "o1",
            "platform": "zepto",
            "ordered_at": "2026-07-19T12:00:00+00:00",
            "grand_total_paise": 5000,
        }
    ]

    first = render_digest_html(orders, [], [], now=_NOW)
    second = render_digest_html(orders, [], [], now=_NOW)

    assert first == second
