"""Unit tests for the migration runner's selection logic.

The database interaction is exercised by actually deploying; what is worth
locking down here is the ordering and the already-applied filtering, because
getting either wrong silently corrupts production schema state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.migrate import (
    discover_migrations,
    pending_migrations,
    require_database_url,
    unreachable_host_hint,
)


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("select 1;", encoding="utf-8")
    return path


def test_discover_migrations_returns_files_in_numeric_order(tmp_path: Path) -> None:
    # Created out of order on purpose: ordering must come from the filename.
    _touch(tmp_path, "0010_tenth.sql")
    _touch(tmp_path, "0002_second.sql")
    _touch(tmp_path, "0001_first.sql")

    assert [path.name for path in discover_migrations(tmp_path)] == [
        "0001_first.sql",
        "0002_second.sql",
        "0010_tenth.sql",
    ]


def test_discover_migrations_ignores_non_sql_files(tmp_path: Path) -> None:
    _touch(tmp_path, "0001_first.sql")
    (tmp_path / "README.md").write_text("not a migration", encoding="utf-8")

    assert [path.name for path in discover_migrations(tmp_path)] == ["0001_first.sql"]


def test_pending_migrations_skips_already_applied_and_keeps_order(tmp_path: Path) -> None:
    first = _touch(tmp_path, "0001_first.sql")
    second = _touch(tmp_path, "0002_second.sql")
    third = _touch(tmp_path, "0003_third.sql")

    outstanding = pending_migrations([first, second, third], {"0001_first"})

    assert [path.stem for path in outstanding] == ["0002_second", "0003_third"]


def test_pending_migrations_is_empty_when_everything_is_applied(tmp_path: Path) -> None:
    first = _touch(tmp_path, "0001_first.sql")

    assert pending_migrations([first], {"0001_first"}) == []


def test_require_database_url_fails_loudly_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # A silent skip would let a deploy succeed against an un-migrated database,
    # which is exactly the failure this script exists to prevent.
    with pytest.raises(SystemExit):
        require_database_url()


def test_require_database_url_forces_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host:5432/postgres")

    assert require_database_url().endswith("?sslmode=require")


def test_require_database_url_preserves_existing_query_and_sslmode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://user:pw@host:5432/postgres?sslmode=verify-full"
    )

    # Already explicit — must not be appended to twice.
    assert require_database_url().count("sslmode=") == 1


def test_unreachable_host_hint_explains_the_ipv6_only_direct_host() -> None:
    """The deploy failed with a bare "Network is unreachable" against an IPv6
    address, which says nothing about the cause or the fix.
    """
    hint = unreachable_host_hint(
        "postgresql://postgres:pw@db.ref.supabase.co:5432/postgres",
        'connection to server at "2406:da12:1f1:f800::1", port 5432 failed: Network is unreachable',
    )

    assert hint is not None
    assert "pooler" in hint


def test_unreachable_host_hint_is_silent_when_already_using_the_pooler() -> None:
    # Same symptom, different cause — pointing at the pooler would be wrong.
    hint = unreachable_host_hint(
        "postgresql://postgres.ref:pw@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres",
        "connection failed: Network is unreachable",
    )

    assert hint is None


def test_unreachable_host_hint_is_silent_for_unrelated_failures() -> None:
    hint = unreachable_host_hint(
        "postgresql://postgres:pw@db.ref.supabase.co:5432/postgres",
        "FATAL: password authentication failed for user",
    )

    assert hint is None
