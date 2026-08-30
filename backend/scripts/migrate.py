"""Apply pending SQL migrations to the Supabase Postgres database.

Runs on every deploy. Migrations are tracked in `public.schema_migrations`, so
already-applied files are skipped and the script is safe to re-run — the
migration files themselves use plain `create table`, which would fail on a
second run without this bookkeeping.

Each migration is applied inside its own transaction together with the row that
records it, so a failure can never leave a migration half-applied yet marked as
done. The script exits non-zero on the first failure, which fails the deploy
before the new revision starts serving traffic against a schema it expects.

Usage:  DATABASE_URL=postgresql://... python scripts/migrate.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# psycopg is intentionally NOT a backend runtime dependency — the app talks to
# Postgres through PostgREST. The deploy installs it just for this script, so
# the import is deferred to keep the pure helpers (and their tests) importable
# without it. Type checkers and linters still need the name to exist, hence the
# TYPE_CHECKING import; `from __future__ import annotations` keeps the
# annotation itself from being evaluated at runtime.
if TYPE_CHECKING:
    import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"

TRACKING_TABLE_DDL = """
create table if not exists public.schema_migrations (
    version text primary key,
    applied_at timestamptz not null default now()
)
"""


def discover_migrations(directory: Path) -> list[Path]:
    """Every .sql migration in the directory, ordered by filename.

    Filenames are zero-padded (0001_, 0002_, ...) so a lexicographic sort is
    also the intended execution order.
    """
    return sorted(directory.glob("*.sql"))


def pending_migrations(available: list[Path], applied: set[str]) -> list[Path]:
    """The migrations that still need applying, in order."""
    return [path for path in available if path.stem not in applied]


def unreachable_host_hint(url: str, error: str) -> str | None:
    """Explain an unroutable-address failure, when that is what happened.

    Supabase's direct host (`db.<ref>.supabase.co`) publishes only an AAAA
    record, and GitHub-hosted runners have no IPv6 route — so the deploy fails
    with a bare "Network is unreachable" against an address nobody recognises.
    The fix is the Supavisor pooler, which is reachable over IPv4; say so here
    rather than leaving the next person to rediscover it.
    """
    if "network is unreachable" not in error.lower():
        return None
    if "pooler.supabase.com" in url:
        return None
    return (
        "The database host appears to be IPv6-only and this runner has no IPv6 "
        "route. Use the Supavisor session-mode pooler instead: Supabase > "
        "Project Settings > Database > Connection string > Session pooler "
        "(host looks like aws-N-<region>.pooler.supabase.com, user "
        "postgres.<project-ref>). Update the SUPABASE_DB_URL secret, and "
        "percent-encode any reserved characters in the password."
    )


def tenant_not_found_hint(url: str, error: str) -> str | None:
    """Explain a Supavisor "tenant/user not found" failure, when that is what happened.

    Supavisor returns this when the project reference encoded in the pooler
    username no longer resolves — typically because the Supabase project
    behind `SUPABASE_DB_URL` was paused or deleted, or the secret itself is
    stale (wrong project ref or password). It is never caused by anything in
    this script or the migration files; say so here rather than leaving the
    next person to suspect a code regression.
    """
    lowered = error.lower()
    if "tenant" not in lowered or "not found" not in lowered:
        return None
    return (
        "Supavisor rejected the connection: it doesn't recognise the project "
        "reference in the pooler username. This means the Supabase project is "
        "paused/deleted, or the SUPABASE_DB_URL secret is stale — not a code "
        "or migration problem. Check Supabase > Project Settings for the "
        "project's status, then refresh the SUPABASE_DB_URL secret with a "
        "current connection string (Project Settings > Database > Connection "
        "string > Session pooler) if the project ref or password changed."
    )


def require_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set. Add the SUPABASE_DB_URL repository secret "
            "(Supabase > Project Settings > Database > Connection string)."
        )
    # Supabase requires TLS; be explicit rather than relying on libpq defaults.
    if "sslmode=" not in url:
        url += "&sslmode=require" if "?" in url else "?sslmode=require"
    return url


def apply_migrations(connection: psycopg.Connection, migrations: list[Path]) -> int:
    applied_count = 0
    for path in migrations:
        sql = path.read_text(encoding="utf-8")
        # One transaction per migration: the DDL and its bookkeeping row commit
        # together, so we can never record a migration that did not fully apply.
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "insert into public.schema_migrations (version) values (%s)",
                    (path.stem,),
                )
        print(f"applied  {path.name}", flush=True)
        applied_count += 1
    return applied_count


def main() -> int:
    import psycopg  # deferred: see note at the top of the module

    if not MIGRATIONS_DIR.is_dir():
        raise SystemExit(f"migrations directory not found: {MIGRATIONS_DIR}")

    database_url = require_database_url()
    available = discover_migrations(MIGRATIONS_DIR)
    if not available:
        print("no migration files found; nothing to do")
        return 0

    try:
        connect = psycopg.connect(database_url, autocommit=True)
    except psycopg.OperationalError as exc:
        hint = unreachable_host_hint(database_url, str(exc)) or tenant_not_found_hint(
            database_url, str(exc)
        )
        raise SystemExit(f"{exc}\n\n{hint}" if hint else str(exc)) from exc

    with connect as connection:
        with connection.cursor() as cursor:
            cursor.execute(TRACKING_TABLE_DDL)
            cursor.execute("select version from public.schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}

        outstanding = pending_migrations(available, applied)
        for path in available:
            if path.stem in applied:
                print(f"skipped  {path.name} (already applied)", flush=True)

        if not outstanding:
            print("database is up to date")
            return 0

        count = apply_migrations(connection, outstanding)
        print(f"done: {count} migration(s) applied")

    return 0


if __name__ == "__main__":
    sys.exit(main())
