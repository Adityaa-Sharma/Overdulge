# ADR-0004: "Usuals" ranking source and live recommendation matching

Status: Accepted
Context issue: #8 (FR-7 Usuals, recommendations & reorder redirect)

## Context

FR-7 combines three heterogeneous frequency sources into one "usuals" list
(BRD §2.9): Zepto's pre-aggregated `get_past_order_items` (stable
`productVariantId` keys), Instamart's pre-aggregated `your_go_to_items`, and
Swiggy Food, which has no equivalent frequency tool — Food usuals must be
computed locally from synced `order_items` (FR-2 canonical schema, BRD §5).

FR-7.2 then needs a cheaper-and/or-lower-calorie alternative per frequent
item, grounded in a live `search_products`/`search_menu` call made at request
time (AC-3 — explicitly not stale/cached). This requires comparing a live
search result against the frequent item on price and calorie axes, and
FR-6 (calorie estimation) is the only place calorie-from-item-name mapping
logic exists — but FR-6's design point is ingest-time estimation for already
synced order items, not on-demand estimation for a name that only exists in
an ephemeral live search response. Reorder itself (FR-7.3) must resolve to a
platform deep link, and platform-specific link shapes are exactly the kind
of knowledge SYSTEM.md §2 confines to `mcp/adapters/*`.

## Decision

1. **Food usuals join key**: since `order_items.platform_item_id` is not
   guaranteed stable for Swiggy Food across orders (BRD has no equivalent
   guarantee to Zepto's `productVariantId`, §2.9), Food frequency is computed
   by grouping synced `order_items` on `lower(trim(name))`. Zepto keeps
   `product_variant_id` as its join key per §2.9 (AC-2); Instamart keeps
   whatever key `your_go_to_items` returns natively. Mixing join semantics
   per platform is intentional, not an inconsistency to "fix" — each
   platform's own frequency substrate is trusted over a synthetic unified
   key.
2. **No suggestion caching/persistence.** `search_products`/`search_menu`
   results backing FR-7.2 are fetched synchronously per request and never
   written to a table or cache. "Usuals" (FR-7.1) may be served from a
   request-time call to the two pre-aggregated tools plus a query over
   already-synced `order_items` — no new persisted table either. This keeps
   the whole recommendations path read-only and trivially consistent with
   NFR-4 (no fabrication) since every number shown is either straight from a
   platform response or a DB aggregate computed at request time.
3. **Calorie estimation is a shared function, not duplicated logic.** FR-6
   must expose its item-name → calorie-estimate mapping as a standalone
   callable (`llm/calories.py::estimate_calories`), used both at ingest
   (FR-6's own pipeline) and on-demand against live search-result item names
   in the FR-7.2 comparison. The FR-7 task that builds alternative-matching
   is blocked on FR-6 shipping this function, not on FR-6's full rollup UI.
4. **Alternative-selection criteria**: a candidate qualifies if it is
   cheaper (`unit_price_paise` lower than the frequent item's own average) OR
   lower-calorie, evaluated only when *both* the frequent item and the
   candidate have a calorie estimate (i.e., both are in FR-6.1's
   ready-to-eat/food-delivery scope). Non-ready-to-eat grocery items are
   never assigned a synthetic calorie estimate just to enable a comparison —
   for those, cheaper-price is the only axis. If no live candidate qualifies
   on either axis, FR-7.2's AC-5 applies: omit the suggestion, do not render
   a placeholder.
5. **Redirect URLs are adapter output, not route logic.** Every adapter
   wrapper added for `get_past_order_items`, `your_go_to_items`,
   `search_products`, and `search_menu` returns a normalized dict that
   includes a `redirect_url` field pointing at the item/restaurant on the
   platform's own site/app. `api/recommendations.py` only passes this
   through; it never constructs a platform URL itself, keeping platform
   knowledge inside `mcp/adapters/*` per the existing module-boundary rule.

## Consequences

- FR-7's task breakdown has a real dependency edge on FR-2 (canonical
  schema + synced Food order_items for the local frequency ranking, and the
  generic `mcp/client.py` JSON-RPC client both features need) and on FR-6
  (the shared calorie-estimation function). Neither task set can reach
  `ready-for-dev` until those land — reflected as `blocked-by` on the parent
  feature issues (#3, #7) rather than on task numbers that don't exist yet.
- Because nothing is cached, FR-7.2's response time is bounded by the
  slowest live `search_products`/`search_menu` call per frequent item;
  no task in this breakdown may introduce a persistence layer to "speed
  this up" without a follow-up ADR — that would violate AC-3's
  not-stale guarantee.
- Grocery-only users (Zepto/Instamart-only, no Swiggy Food) will see
  cheaper-only suggestions for most items and that is correct, not a
  degraded case to special-case away.
