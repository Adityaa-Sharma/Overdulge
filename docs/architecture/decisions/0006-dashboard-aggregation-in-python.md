# ADR-0006: Dashboard/analytics aggregation computed in Python, not DB views or RPC

Status: Accepted
Context issue: #4 (FR-3 Spend dashboard)

## Context

FR-3 needs several aggregate reads over `orders`/`order_items`: spend totals
and trend series bucketed by week/month, per-platform and combined; category
and top-N-by-spend breakdowns; order frequency and average order value;
current-month run-rate projection; spend by delivery `address_id`. FR-4 (NL
query engine, #5) and FR-5 (budgeting progress, #6) will need the same kind
of aggregate reads over the same tables shortly after.

`core/db.py` (ADR-0002) is deliberately generic: "No table-specific code
lives here — generic select/insert/upsert/delete helpers over PostgREST's
REST conventions only." PostgREST can expose aggregates two ways: Postgres
views/functions called through `/rest/v1/<view>` or `/rest/v1/rpc/<fn>`, or
plain row `select` with the caller aggregating client-side. The first means
every new aggregate needs its own migration, its own RLS policy or
`security_invoker` view semantics, and a second place (SQL) encoding
business logic like "what counts as this month" or "how is a category
computed" — alongside the Python encoding of the same rules elsewhere.

Phase 1 scale is fixed (BRD §1: <25 users, friends-beta), so the row counts
a single dashboard load touches are small (one user's order history, not a
cross-user scan).

## Decision

Dashboard (and later query/budget) aggregation is computed in Python, in a
new `backend/app/analytics/` package, over rows fetched through the existing
`user_client` PostgREST wrapper (`core/db.py`, RLS-scoped to the caller via
JWT forwarding per ADR-0002). No new Postgres views, functions, or `/rpc/`
surface for read aggregation in Phase 1.

`app/analytics/aggregate.py` holds pure functions (rows in, aggregate dicts
out — no I/O, no FastAPI, no PostgREST, no `datetime.now()` inside; "now" is
always an explicit parameter) so the same logic is unit-testable without
fixtures hitting a fake HTTP layer, and reusable from `api/dashboard.py`
today and `llm/agent.py` (FR-4) / budgeting progress (FR-5) later. The route
handler in `api/dashboard.py` owns the PostgREST fetch (scoped by
`is_cancelled=eq.false` via `db.py`'s existing `filters` param, per
ADR-0005 §5: cancelled-order exclusion is a read-time concern owned here)
and hands rows to `analytics/aggregate.py`.

This ADR adds `backend/app/analytics/` to the module boundaries in
`docs/architecture/SYSTEM.md` §2. Per the current architecture-note process
(this PR touches new files only, never the shared SYSTEM.md), that edit is
filed as its own follow-up `type:task` issue rather than made here.

## Consequences

- One place (Python) encodes "this month", category rollups, and the
  projection formula — no parallel SQL implementation to keep in sync, no
  new RLS/grants surface per aggregate.
- `analytics/aggregate.py` functions take plain row lists (plus an explicit
  `now` where time-bucketing matters), so tests supply fixture rows and a
  fixed `now` directly — no need to stand up or mock PostgREST, and no
  flakiness from wall-clock time in tests.
- Trade-off: does not scale past roughly a few thousand order rows fetched
  per request; acceptable for NFR-3 phase-1 scale (order history for <25
  personal accounts, Instamart windowed to ~15 days per BRD §2.3). If this
  stops holding, revisit with DB-side materialized views — a future ADR, not
  a Phase 1 blocker.
- FR-4 and FR-5 should reuse `analytics/aggregate.py` rather than
  reimplementing spend/category logic against raw rows.
- Dashboard figures are computed live on every request from current DB
  state — no caching layer, no precomputed/stale snapshot (BRD AC-7). The
  only staleness in the system is the gap between "last sync" and "now",
  which is already exposed via FR-2's `GET /api/v1/sync/status`
  (`last_sync_at`); the dashboard surfaces that timestamp alongside its
  figures rather than inventing a second freshness mechanism.
