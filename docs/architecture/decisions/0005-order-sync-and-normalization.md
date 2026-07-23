# ADR-0005: Order sync pipeline — MCP client, adapter/normalize split, and sync orchestration

Status: Accepted
Context issue: #3 (FR-2 Order sync & normalization)

## Context

BRD §2.1/§2.4-§2.8 and FR-2 require pulling order history from three
platform-side MCP tool namespaces, normalizing three different money/time
formats into one canonical schema (BRD §5), and doing it both on a daily
Cloudflare Cron Trigger and on-demand from a "Sync now" button, without
duplicating rows and without ever touching a mutating tool.

One fact shapes the whole design: `linked_accounts.platform` (migration
0003) has only two values, `swiggy` and `zepto` — a single Swiggy OAuth link
authorizes both `get_food_orders` (Swiggy Food) and `get_orders` (Instamart),
which are separate MCP tool namespaces on the same account. The canonical
`orders.platform` enum (BRD §5) needs three values:
`swiggy_food`, `swiggy_instamart`, `zepto`. Sync therefore fans one linked
account (`swiggy`) out into two independent sync sub-flows.

Token refresh (ADR-0001: "on sync or on-demand platform calls") is already
implemented in `oauth/engine.py` (`refresh_tokens`/`decode_tokens`, landed
with #17). FR-2's tasks reuse those functions directly rather than
re-implementing refresh logic; only the sync-orchestration task actually
calls them.

## Decision

### 1. Generic MCP client, platform-blind
`mcp/client.py` is a JSON-RPC-over-streamable-HTTP client with no platform
knowledge: `call_tool(base_url, access_token, tool_name, params) -> dict`.
It knows nothing about Swiggy, Instamart, or Zepto — those live entirely in
`mcp/adapters/*`, per the existing module-boundary rule in SYSTEM.md §2.
It does not perform token refresh itself; a 401 propagates to the caller
(sync orchestration), which is the only layer that knows how to refresh.

### 2. Adapters emit a shared intermediate type; normalize.py never touches money
Each adapter (`mcp/adapters/{swiggy_food,swiggy_instamart,zepto}.py`)
exposes `fetch_orders(client, base_url, access_token) -> list[NormalizedOrder]`,
where `NormalizedOrder`/`NormalizedOrderItem` (defined once in
`sync/normalize.py`, imported by adapters — the canonical shape is defined
where the canonical schema is defined) already carry paise ints and
timezone-aware UTC datetimes. All platform-specific parsing — Swiggy Food's
`"₹273"` string, Instamart's plain-rupee-to-paise conversion, Zepto's
integer-paise passthrough, and all three timestamp quirks (BRD §2.6) —
happens inside the adapter, not in `normalize.py`. `grand_total_paise` is
copied verbatim from each platform's `grandTotal`; no arithmetic is
performed on it anywhere in `mcp/adapters/*` or `sync/normalize.py` (BRD
§2.8 — this is a structural guarantee, not just a convention, and is worth
grepping for in review: no `+`/`sum(` touching `*_paise` fields outside
tests).

`sync/normalize.py` is intentionally thin: given a `linked_account` and the
list of `NormalizedOrder`s an adapter already produced, it attaches
`user_id`, sets `is_cancelled` from the adapter-reported status, and shapes
the final upsert payload for `orders`/`order_items`. It is the only module
that talks to `db.py` for order data, and it is platform-agnostic — it never
branches on `platform`.

### 3. Per-platform quirk resolution, inside each adapter
- **Swiggy Food** (§2.4, §2.6): call `get_addresses`, then `get_food_orders`
  per `addressId`, merging into one list keyed by order id (an order cannot
  appear under two addresses). Food's list-view timestamp has no year;
  resolve it by calling `get_food_order_details` for every order on every
  sync (Phase 1 has no cache for this — acceptable at <25-user, ~daily-sync
  volume; noted as a future optimization, not a blocker).
- **Swiggy Instamart** (§2.7): `get_orders` is always called with
  `orderType` omitted or explicitly `"DASH"` — `"INSTAMART"` is never a
  valid value anywhere in this adapter, enforced by a unit test asserting
  the literal parameter sent, not just the response handling.
- **Zepto** (§2.6): `list_order_history` returns no date; resolve via
  `get_order_detail` per order, same per-sync-call tradeoff as Food.

### 4. One sync core, called from two triggers, with an in-flight lock
`sync/cron.py` exposes `run_sync_for_account(client, linked_account) ->
SyncResult`, the single implementation of "sync one linked account": resolve
a valid access token (delegating to `oauth/engine.py`'s refresh — this
function is the one place in FR-2 that imports from `oauth/`), call the
relevant adapter(s) (both Food and Instamart for a `swiggy` row), normalize,
upsert, and update state. `linked_accounts.last_sync_at` (the column, BRD
§5) is the single source of truth for "when did this account last
*successfully* finish syncing" — it is written only on the success path, and
nowhere else duplicates that value under a different name. Everything else
about the in-progress/lock state lives in the `sync_state` jsonb column;
see §6 for its exact shape.

- The **Cron Trigger entrypoint** (`wrangler.toml` `[triggers] crons`,
  daily) loops `run_sync_for_account` over every `linked_accounts` row
  (service-role client, ADR-0002).
- The **manual "Sync now" route** (`POST /api/v1/sync/{platform}`,
  user-JWT-forwarding) calls it for the one row belonging to the
  authenticated user, guarded by an in-flight lock: before running, it
  checks `sync_state.status != "syncing"`; if already syncing, returns 409
  rather than starting a second concurrent run. Immediately before calling
  `run_sync_for_account`, the route sets `sync_state.status = "syncing"` and
  `sync_state.syncing_since` to the current UTC timestamp — this is the
  field the staleness check reads. In a `finally`, it sets `sync_state.status
  = "idle"`, clears `syncing_since`, and sets `last_error` (or clears it on
  success), and only on success does it also write `linked_accounts.last_sync_at`.
  A crashed sync therefore leaves `status: "syncing"` with a stale
  `syncing_since` rather than wedging the lock forever: the route treats a
  `"syncing"` status whose `syncing_since` is older than a generous timeout
  (e.g. 10 minutes, longer than any plausible real sync) as stale and
  proceeds anyway.

### 5. Cancelled orders and the Instamart window warning are read-time concerns
Sync's only job is to store `is_cancelled` correctly (from the platform's
order status) and to keep `raw` intact. "Excluded from spend totals by
default" (BRD AC-9) is enforced by dashboard aggregation queries
(`is_cancelled = false` by default), which is FR-3's scope (#4) — FR-2 tasks
below stop at correct storage, and this is called out explicitly in the
FR-3 breakdown as an input contract, not re-litigated here.

The Instamart "your window is ~15 days" warning (BRD AC-10) is a static,
always-present note in the sync status response for `platform ==
"swiggy_instamart"` — it is a property of the platform, not something
computed from sync history, so no extra tracking state is needed.

### 6. `sync_state` shape, and the status/manual-trigger API contract
`linked_accounts` has one row per `(user_id, platform)` where `platform` is
the *link*-level enum (`swiggy`/`zepto`, two values) — but AC-10 asks for
sync status "per platform" using the three-value `orders.platform` sense
(`swiggy_food`/`swiggy_instamart`/`zepto`), because Food and Instamart are
counted separately. `run_sync_for_account` therefore writes
`orders_captured_last_run` as an object keyed by `orders.platform`, not a
bare int, so the `swiggy` row can report both sub-platforms from one lock.

`sync_state` carries only the fields that are *not* already a column:
`status` (`"idle" | "syncing"`), `last_error` (string or `null`),
`syncing_since` (the §4 lock-staleness anchor: an ISO-8601 UTC timestamp set
the instant `status` flips to `"syncing"`, cleared back to `null` when it
flips back to `"idle"` — never used for anything except the staleness
check), and `orders_captured_last_run`. It never repeats
`linked_accounts.last_sync_at` under another key — that column is read
directly wherever "last successful sync" is needed.

```json
// idle, after a successful run
{
  "status": "idle",
  "last_error": null,
  "syncing_since": null,
  "orders_captured_last_run": {"swiggy_food": 4, "swiggy_instamart": 1}
}
```

```json
// mid-run, lock held
{
  "status": "syncing",
  "last_error": null,
  "syncing_since": "2026-07-23T03:00:00Z",
  "orders_captured_last_run": {"swiggy_food": 4, "swiggy_instamart": 1}
}
```

Zepto's row uses the same shape with a single-key
`orders_captured_last_run` (`{"zepto": N}`) rather than a bare int, so both
the status endpoint and its tests handle one shape, not two. `GET
/api/v1/sync/status` (user-JWT-forwarding) flattens both `linked_accounts`
rows into one array of exactly the three `orders.platform` values (omitting
ones with no linked account at all — a platform the user never linked has
no row and is left out, not shown as an empty/error entry):
`[{platform, last_sync_at, orders_captured_last_run, warning}]`, where
`last_sync_at` is read straight from the `linked_accounts` column (`null`
if never synced), and `warning` is the static Instamart string (§5) for
`swiggy_instamart` and `null` otherwise. `POST /api/v1/sync/{platform}`
takes an `orders.platform` value too (`swiggy_food` and `swiggy_instamart`
both resolve to the underlying `swiggy` `linked_accounts` row and, per §4,
still run *both* sub-platform adapters in one call — a manual sync of
either Swiggy surface refreshes both, since they share one lock and one
token) and returns the same per-platform-keyed shape as its response body.

## Consequences

- Adapters are the only place that can violate NFR-1 by construction (they
  are the only code that calls MCP tools with arguments); this keeps the
  NFR-1 static denylist check (#9) narrow and effective.
- `run_sync_for_account` being one function used by both triggers means the
  in-flight lock, error handling, and `sync_state` bookkeeping are written
  and tested once.
- Per-order detail calls for Food and Zepto timestamp resolution are an
  accepted N+1 cost at Phase-1 scale; revisit only if sync duration becomes
  a real problem.
- A single `swiggy` linked account produces rows under two different
  `orders.platform` values — any future code that assumes "one linked
  account = one platform value in orders" is wrong; the sync orchestration
  task and its tests must cover the fan-out explicitly.
