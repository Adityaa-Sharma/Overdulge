import logging

import pytest

from app.core import safe_log


def test_log_event_with_allowlisted_fields_emits_via_stdlib_logging(caplog):
    with caplog.at_level(logging.INFO, logger="overdulge"):
        safe_log.log_event(
            "info",
            "sync completed",
            user_id="11111111-1111-1111-1111-111111111111",
            platform="swiggy_food",
            duration_ms=42,
        )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "sync completed"
    assert record.user_id == "11111111-1111-1111-1111-111111111111"
    assert record.platform == "swiggy_food"
    assert record.duration_ms == 42


def test_log_event_rejects_non_allowlisted_field_and_never_logs(caplog):
    with caplog.at_level(logging.INFO, logger="overdulge"):
        with pytest.raises(ValueError, match="order_total"):
            safe_log.log_event("info", "order synced", order_total=4200)

    assert caplog.records == []


def test_log_event_rejects_mixed_allowlisted_and_disallowed_fields(caplog):
    with caplog.at_level(logging.INFO, logger="overdulge"):
        with pytest.raises(ValueError, match="token"):
            safe_log.log_event("error", "sync failed", user_id="u1", token="secret")

    assert caplog.records == []
