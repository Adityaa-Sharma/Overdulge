from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_safe_log_guard import _RAW_LOGGING_PATTERN, find_violations


@pytest.mark.parametrize(
    "line",
    [
        "import logging",
        "logging.debug('x')",
        "logging.info('x')",
        "logging.warning('x')",
        "logging.error('x')",
        "logging.exception('x')",
        "logging.critical('x')",
    ],
)
def test_pattern_matches_each_forbidden_form(line):
    assert _RAW_LOGGING_PATTERN.search(line), f"pattern did not match {line!r}"


@pytest.mark.parametrize(
    "line",
    [
        "from app.core.safe_log import log_event",
        "log_event('info', 'sync completed', user_id=user_id)",
        "# logging is handled via core.safe_log",
        "importlogging",
    ],
)
def test_pattern_ignores_allowed_forms(line):
    assert not _RAW_LOGGING_PATTERN.search(line), f"pattern incorrectly matched {line!r}"


def test_find_violations_flags_raw_logging_outside_safe_log(tmp_path):
    app_root = tmp_path / "app"
    (app_root / "core").mkdir(parents=True)
    offender = app_root / "sync" / "cron.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("import logging\n\nlogging.info('sync tick')\n")

    violations = find_violations(app_root)

    assert len(violations) == 2
    assert all("cron.py" in violation for violation in violations)


def test_find_violations_excludes_safe_log_module(tmp_path):
    app_root = tmp_path / "app"
    core = app_root / "core"
    core.mkdir(parents=True)
    safe_log = core / "safe_log.py"
    safe_log.write_text("import logging\n\n_logger = logging.getLogger('overdulge')\n")

    violations = find_violations(app_root, excluded={safe_log})

    assert violations == []


def test_find_violations_is_clean_against_the_real_backend_app_tree():
    real_app_root = Path(__file__).resolve().parent.parent.parent / "app"

    assert find_violations(real_app_root) == []
