# FR-3: Spend dashboard — architecture note

Parent issue: #4
Related: ADR-0006 (Python-side aggregation), ADR-0002 (Supabase access
pattern), ADR-0005 (order sync — cancelled-order handling, address_id, the
`swiggy_food`/`swiggy_instamart`/`zepto` three-value platform split)

## 1. Summary

One endpoint, `GET /api/v1/dashboard`, returns every figure the dashboard
route needs in one response, computed live from `orders`/`order_items` via
`analytics/aggregate.py` (ADR-0006). No new Postgres views/RPCs. One new
nullable column, `orders.vendor_name`, is required for AC-4's "top
restaurants" (see §4). No new frontend dependency (see §5).

## 2. Data inputs

The route fetches, scoped to the authenticated user via `user_client`
(ADR-0002, RLS-enforced — no explicit `user_id` filter needed):

- `orders` where `is_cancelled = eq.false` (ADR-0005 §5: exclusion from
  spend totals is a read-time concern, owned here).
- `order_items` for those same orders (`order_id=in.(...)`).

Both are plain PostgREST `select` calls through the existing
`PostgrestClient.select` (`core/db.py`) — no new client code needed, only
the route and the aggregation functions it calls.

`has_data` in the response (see §3) is computed from a **separate,
unfiltered** count of the user's `orders` (including cancelled), not from
the filtered list above — a user whose only orders are all-cancelled should
still see "no spend yet" content rather than the zero-orders empty state,
since those are different situations. AC-8 only requires the latter
(genuinely zero synced orders) to show the empty state without erroring;
the filtered-list-is-empty case is just "all totals are ₹0", which every
aggregate function below must already handle (see §4, "empty input").

## 3. Response contract — `GET /api/v1/dashboard`

User-JWT-forwarding, no query params in Phase 1. All money fields are
integer paise (BRD §2.5); nothing here reconstructs a total from components
(BRD §2.8) — `grand_total_paise` is summed/grouped verbatim, never
recomputed from `order_items`.

```json
{
  "generated_at": "2026-07-23T10:00:00Z",
  "has_data": true,
  "totals": {
    "this_week_paise": {"combined": 0, "swiggy_food": 0, "swiggy_instamart": 0, "zepto": 0},
    "this_month_paise": {"combined": 0, "swiggy_food": 0, "swiggy_instamart": 0, "zepto": 0}
  },
  "trend": {
    "weekly": [{"period_start": "2026-07-14", "combined_paise": 0, "swiggy_food_paise": 0, "swiggy_instamart_paise": 0, "zepto_paise": 0}],
    "monthly": [{"period_start": "2026-06-01", "combined_paise": 0, "swiggy_food_paise": 0, "swiggy_instamart_paise": 0, "zepto_paise": 0}]
  },
  "category_breakdown": {
    "food_delivery_paise": 0,
    "grocery_paise": 0,
    "item_categories_paise": {"snacks": 0}
  },
  "top_restaurants": [{"name": "string", "spend_paise": 0, "order_count": 0}],
  "top_products": [{"name": "string", "spend_paise": 0, "order_count": 0}],
  "order_stats": {
    "swiggy_food": {"order_count": 0, "avg_order_value_paise": null},
    "swiggy_instamart": {"order_count": 0, "avg_order_value_paise": null},
    "zepto": {"order_count": 0, "avg_order_value_paise": null}
  },
  "projection": {
    "month": "2026-07",
    "spend_to_date_paise": {"combined": 0, "swiggy_food": 0, "swiggy_instamart": 0, "zepto": 0},
    "days_elapsed": 23,
    "days_in_month": 31,
    "projected_total_paise": {"combined": 0, "swiggy_food": 0, "swiggy_instamart": 0, "zepto": 0},
    "label": "Projection"
  },
  "location_lens": [{"address_id": "string", "spend_paise": 0, "order_count": 0}]
}
```

Notes:
- `generated_at` and `projection`'s `days_elapsed`/`days_in_month` are all
  derived from one server-side `now` (UTC) captured once at the start of
  request handling and threaded into every aggregate call — never
  `datetime.now()` called independently in multiple places, which could
  otherwise let `totals`/`trend`/`projection` disagree about "today" within
  the same response.
- `top_restaurants` is `swiggy_food` orders grouped by `vendor_name`
  (§4), excluding rows where it's `NULL` (not yet backfilled — see §4).
  `top_products` is `swiggy_instamart` + `zepto` `order_items` grouped by
  `name`. Both capped at 10, ordered by `spend_paise` descending.
- `location_lens` is `swiggy_food` orders grouped by `address_id`,
  excluding `NULL` (BRD §2.4 — only Food has address-scoped history).
- `avg_order_value_paise` is `null` (not `0`) when `order_count` is `0` —
  distinguishes "no orders" from "orders summing to ₹0", same
  not-a-number-when-meaningless discipline ADR-0003 uses for the query
  engine.
- Every list-valued field returns `[]`, every count `0`, every money field
  `0`, `avg_order_value_paise` stays `null`, on zero input rows — no branch
  in the route needed to special-case "no data" beyond `has_data` itself
  (AC-8: renders the empty state without erroring, because nothing errors).

## 4. `orders.vendor_name` — required for AC-4 (top restaurants)

BRD §5's canonical schema (explicitly "(minimum)") has no restaurant/store
identity at the order level — only `order_items.name`, which for Swiggy Food
is a dish, not a restaurant. Grouping Food spend by item name would
fragment one restaurant's spend across every dish ordered from it, which is
not "top restaurants." Instamart/Zepto don't have this problem: their
"top products" (AC-4) are genuinely item-level, so `order_items.name`
grouping is correct for them as-is — no schema change needed there.

Fix: add `vendor_name TEXT NULL` to `orders`, populated only for
`swiggy_food` (restaurant name from the Food order payload; left `NULL` for
`swiggy_instamart`/`zepto`, which don't need it). Since migration `0006`
(task #43, `orders`/`order_items` creation) and the `swiggy_food` adapter
(task #49) are both still open/unimplemented, this is folded into their
existing scope rather than filed as a separate ALTER migration + adapter
patch — see comments left on #43 and #49. `top_restaurants` groups on
`vendor_name`, excluding `NULL` rows; until #49 ships this population, the
field returns an empty list rather than erroring (§3).

No other module boundary changes: `vendor_name` extraction stays inside
`mcp/adapters/swiggy_food.py` (platform-specific knowledge lives only in
`mcp/adapters/*`/`oauth/platforms/*`, SYSTEM.md §2) — `analytics/aggregate.py`
only ever sees the already-normalized column.

## 5. Frontend

`frontend/src/routes/Dashboard.tsx`, one `apiFetch('/dashboard')` call on
mount, sections for each part of §3's contract: totals, trend, category
breakdown, top restaurants/products, order stats, projection (always
rendering the literal "Projection" label next to any projected figure —
AC-5), location lens. Loading/error/populated/empty states per §3's
`has_data`.

Trend and category-breakdown visuals are hand-built (minimal inline SVG/CSS
in `frontend/src/components/charts/`), not a new charting-library
dependency. Reasons: the frontend today has exactly three runtime
dependencies (`react`, `react-router-dom`, `@supabase/supabase-js`); Phase 1
data volume per chart is at most a few dozen points (weekly/monthly
buckets for a <25-user friends-beta), well within what a small hand-rolled
component handles without a library's abstraction; and the repo has a live
recent incident (#25, a typosquatted transitive dependency merged to main)
that argues for not adding dependency surface without a concrete need. If a
richer chart need shows up later (FR-5 budget progress, FR-6 calorie
trends), revisit with a real library then — not a Phase 1 blocker.

## 6. Task breakdown

- **FR-3: Dashboard aggregation core** (`analytics/aggregate.py`) — pure
  functions per §3's contract, unit tests with fixture rows + fixed `now`.
  Independent of every other FR-3 task (only depends on the column
  contract, not on #43's code landing) — ready for dev immediately.
- **FR-3: Dashboard API route** (`GET /api/v1/dashboard`) — fetch +
  assemble §3's response. Blocked by #43 (needs `orders`/`order_items` to
  exist) and the aggregation-core task above.
- **FR-3: Dashboard frontend** (`routes/Dashboard.tsx` + chart components) —
  §5. Blocked by the API route task.
- Follow-up docs task (not part of FR-3's acceptance criteria): add
  `backend/app/analytics/` to `SYSTEM.md` §2's module tree (ADR-0006).
