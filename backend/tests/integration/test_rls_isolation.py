"""RLS cross-user isolation harness (#90/#91, ADR-0009 §3, BRD §6 NFR-2 AC-4).

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

RLS_TABLES covers every user-scoped table with a direct `user_id` column
(`linked_accounts`, `orders`, `budgets`). `order_items` has no `user_id`
column of its own — its RLS policies join back to `orders.user_id`
(ADR-0002) — so it gets its own fixture/tests below instead of a
`RLS_TABLES` entry.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

_DB_URL_ENV_VAR = "RLS_TEST_DATABASE_URL"
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _BACKEND_DIR / "supabase" / "migrations"
_AUTH_STUB_SQL = Path(__file__).resolve().parent / "fixtures" / "auth_stub.sql"


def _unique_order_id() -> str:
    """A fresh value per call — `orders` uniques on (platform, platform_order_id)
    with no user_id in that constraint, so two seed rows for two different
    users can't share a literal seed value the way other tables' seed columns
    do.
    """
    return f"seed-order-{uuid.uuid4()}"


RLS_TABLES = [
    {
        "table": "linked_accounts",
        "user_id_column": "user_id",
        "seed_columns": {"platform": "swiggy", "tokens_encrypted": "seed-encrypted-token"},
    },
    {
        "table": "orders",
        "user_id_column": "user_id",
        "seed_columns": {
            "platform": "zepto",
            "platform_order_id": _unique_order_id,
            "status": "delivered",
            "ordered_at": "2026-01-01T00:00:00+00:00",
            "grand_total_paise": 10000,
            "raw": Jsonb({}),
        },
    },
    {
        "table": "budgets",
        "user_id_column": "user_id",
        "seed_columns": {
            "month": "2026-01-01",
            "category": "__overall__",
            "cap_paise": 500000,
        },
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


def _resolve_seed_values(seed_columns: dict) -> list:
    """Seed column values that are callables get invoked fresh per row —
    needed for columns like `orders.platform_order_id`, unique on their own
    and thus unable to reuse one literal value across user A's and user B's
    seed rows the way a plain constant column can.
    """
    return [value() if callable(value) else value for value in seed_columns.values()]


def _insert_row(cur, table: str, id_column: str, id_value, seed_columns: dict):
    """Inserts a row with `id_column` set to `id_value` plus `seed_columns`,
    returning the new row's id. Generic over which column anchors ownership —
    `user_id` for the direct RLS_TABLES case, `order_id` for order_items'
    join-back case.
    """
    columns = [id_column, *seed_columns.keys()]
    column_list = sql.SQL(", ").join(map(sql.Identifier, columns))
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(columns))
    insert_stmt = sql.SQL(
        "insert into public.{table} ({columns}) values ({values}) returning id"
    ).format(table=sql.Identifier(table), columns=column_list, values=placeholders)
    cur.execute(insert_stmt, [str(id_value), *_resolve_seed_values(seed_columns)])
    return cur.fetchone()[0]


@pytest.fixture
def seeded_table(rls_database_url, request):
    """Seeds one row for user A and one for user B (as the superuser, bypassing RLS),
    then hands the test a connection with an open transaction to assume the
    `authenticated` role in. Rolled back on teardown so tests never see each
    other's seed data.
    """
    entry = request.param
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    table, user_id_column = entry["table"], entry["user_id_column"]
    seed_columns = entry["seed_columns"]
    with psycopg.connect(rls_database_url) as conn:
        with conn.cursor() as cur:
            row_a_id = _insert_row(cur, table, user_id_column, user_a, seed_columns)
            row_b_id = _insert_row(cur, table, user_id_column, user_b, seed_columns)
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
            values = [str(ctx["user_b"]), *_resolve_seed_values(entry["seed_columns"])]
            cur.execute(insert_stmt, values)


_ORDER_SEED_COLUMNS = {
    "platform": "zepto",
    "platform_order_id": _unique_order_id,
    "status": "delivered",
    "ordered_at": "2026-01-01T00:00:00+00:00",
    "grand_total_paise": 10000,
    "raw": Jsonb({}),
}


@pytest.fixture
def seeded_order_items(rls_database_url):
    """order_items has no user_id column — RLS joins back to orders.user_id
    (ADR-0002) — so isolation must be seeded and asserted through that join
    rather than via the RLS_TABLES/seeded_table direct-column case shape.
    """
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    with psycopg.connect(rls_database_url) as conn:
        with conn.cursor() as cur:
            order_a_id = _insert_row(cur, "orders", "user_id", user_a, _ORDER_SEED_COLUMNS)
            order_b_id = _insert_row(cur, "orders", "user_id", user_b, _ORDER_SEED_COLUMNS)
            item_seed_columns = {"name": "seed item", "quantity": 1}
            item_a_id = _insert_row(cur, "order_items", "order_id", order_a_id, item_seed_columns)
            item_b_id = _insert_row(cur, "order_items", "order_id", order_b_id, item_seed_columns)
        yield {
            "conn": conn,
            "user_a": user_a,
            "user_b": user_b,
            "order_b_id": order_b_id,
            "item_a_id": item_a_id,
            "item_b_id": item_b_id,
        }
        conn.rollback()


def test_order_items_select_returns_only_own_row(seeded_order_items):
    ctx = seeded_order_items
    with ctx["conn"].cursor() as cur:
        _assume_user(cur, ctx["user_a"])
        cur.execute("select id from public.order_items")
        rows = cur.fetchall()

    assert [row[0] for row in rows] == [ctx["item_a_id"]]


def test_order_items_select_by_id_excludes_other_users_row(seeded_order_items):
    ctx = seeded_order_items
    with ctx["conn"].cursor() as cur:
        _assume_user(cur, ctx["user_a"])
        cur.execute("select id from public.order_items where id = %s", (ctx["item_b_id"],))
        rows = cur.fetchall()

    assert rows == []


def test_order_items_insert_against_other_users_order_is_rejected(seeded_order_items):
    ctx = seeded_order_items
    with ctx["conn"].cursor() as cur:
        _assume_user(cur, ctx["user_a"])
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security policy"):
            cur.execute(
                "insert into public.order_items (order_id, name, quantity) values (%s, %s, %s)",
                (ctx["order_b_id"], "seed item", 1),
            )
