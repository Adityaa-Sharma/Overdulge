"""RLS cross-user isolation harness (#90, ADR-0009 §3, BRD §6 NFR-2 AC-4).

Applies fixtures/auth_stub.sql then every backend/supabase/migrations/*.sql
file, in order, to a real ephemeral Postgres instance (the
`backend-integration` CI job's `postgres:16-alpine` service container — see
.github/workflows/ci.yml). Mocked PostgREST responses, the pattern every
other data-access test uses, can never prove RLS: the mock doesn't run
Postgres, so it cannot catch a missing or wrong `USING` clause. This module
connects directly with psycopg, `SET ROLE authenticated`, and confirms
Postgres itself enforces `auth.uid() = user_id` — never through app/, which
stays on PostgREST-over-HTTP exclusively (ADR-0002).

Skipped unless RLS_TEST_DATABASE_URL is set, so the fast unit-test job (and
a plain local `pytest` run with no Postgres available) isn't affected.

RLS_TABLES currently covers only `linked_accounts` — orders/order_items/
budgets are out of scope for #90 (blocked on #43/#67 per the parent issue,
#9) and get appended here once those tables exist.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

_DB_URL_ENV_VAR = "RLS_TEST_DATABASE_URL"
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _BACKEND_DIR / "supabase" / "migrations"
_AUTH_STUB_SQL = Path(__file__).resolve().parent / "fixtures" / "auth_stub.sql"

RLS_TABLES = [
    {
        "table": "linked_accounts",
        "user_id_column": "user_id",
        "seed_columns": {"platform": "swiggy", "tokens_encrypted": "seed-encrypted-token"},
    },
]

pytestmark = pytest.mark.skipif(
    not os.environ.get(_DB_URL_ENV_VAR),
    reason=(
        f"{_DB_URL_ENV_VAR} not set — this test requires a live ephemeral Postgres "
        "(see the backend-integration job in .github/workflows/ci.yml)"
    ),
)


def _apply_sql_file(db_url: str, sql_file: Path) -> None:
    subprocess.run(
        ["psql", db_url, "-v", "ON_ERROR_STOP=1", "-f", str(sql_file)],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def rls_database_url() -> str:
    db_url = os.environ[_DB_URL_ENV_VAR]
    _apply_sql_file(db_url, _AUTH_STUB_SQL)
    for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        _apply_sql_file(db_url, migration)
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "grant select, insert, update, delete on all tables in schema public to authenticated"
        )
    return db_url


@pytest.fixture
def seeded_table(rls_database_url, request):
    """Seeds one row for user A and one for user B (as the superuser, bypassing RLS),
    then hands the test a connection with an open transaction to assume the
    `authenticated` role in. Rolled back on teardown so tests never see each
    other's seed data.
    """
    entry = request.param
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    columns = [entry["user_id_column"], *entry["seed_columns"].keys()]
    column_list = sql.SQL(", ").join(map(sql.Identifier, columns))
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(columns))
    insert_stmt = sql.SQL(
        "insert into public.{table} ({columns}) values ({values}) returning id"
    ).format(table=sql.Identifier(entry["table"]), columns=column_list, values=placeholders)

    with psycopg.connect(rls_database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(insert_stmt, [str(user_a), *entry["seed_columns"].values()])
            row_a_id = cur.fetchone()[0]
            cur.execute(insert_stmt, [str(user_b), *entry["seed_columns"].values()])
            row_b_id = cur.fetchone()[0]
        yield {
            "entry": entry,
            "conn": conn,
            "user_a": user_a,
            "user_b": user_b,
            "row_a_id": row_a_id,
            "row_b_id": row_b_id,
        }
        conn.rollback()


def _assume_user(cur, user_id: uuid.UUID) -> None:
    cur.execute("set local role authenticated")
    cur.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))


@pytest.mark.parametrize("seeded_table", RLS_TABLES, indirect=True, ids=lambda e: e["table"])
def test_select_returns_only_own_row(seeded_table):
    ctx = seeded_table
    with ctx["conn"].cursor() as cur:
        _assume_user(cur, ctx["user_a"])
        cur.execute(
            sql.SQL("select id from public.{table}").format(
                table=sql.Identifier(ctx["entry"]["table"])
            )
        )
        rows = cur.fetchall()

    assert [row[0] for row in rows] == [ctx["row_a_id"]]


@pytest.mark.parametrize("seeded_table", RLS_TABLES, indirect=True, ids=lambda e: e["table"])
def test_select_by_id_excludes_other_users_row(seeded_table):
    ctx = seeded_table
    with ctx["conn"].cursor() as cur:
        _assume_user(cur, ctx["user_a"])
        cur.execute(
            sql.SQL("select id from public.{table} where id = %s").format(
                table=sql.Identifier(ctx["entry"]["table"])
            ),
            (ctx["row_b_id"],),
        )
        rows = cur.fetchall()

    assert rows == []


@pytest.mark.parametrize("seeded_table", RLS_TABLES, indirect=True, ids=lambda e: e["table"])
def test_insert_with_other_users_id_is_rejected(seeded_table):
    ctx = seeded_table
    entry = ctx["entry"]
    columns = [entry["user_id_column"], *entry["seed_columns"].keys()]
    column_list = sql.SQL(", ").join(map(sql.Identifier, columns))
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(columns))
    insert_stmt = sql.SQL("insert into public.{table} ({columns}) values ({values})").format(
        table=sql.Identifier(entry["table"]), columns=column_list, values=placeholders
    )

    with ctx["conn"].cursor() as cur:
        _assume_user(cur, ctx["user_a"])
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security policy"):
            cur.execute(insert_stmt, [str(ctx["user_b"]), *entry["seed_columns"].values()])
