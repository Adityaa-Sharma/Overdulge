# ADR-0004: Budget progress aggregation, grounded cut-suggestions, and weekly digest design

Status: Accepted
Context issue: #6 (FR-5 Budgeting)

## Context

FR-5 (BRD §4) needs: caps (overall + per-category), progress display, LLM
"where to cut" suggestions grounded in real order lines, and a weekly email
digest. It reads the canonical `orders`/`order_items` schema (BRD §5) that
FR-2 (order sync & normalization, issue #3) owns but has not yet built —
`orders`/`order_items` do not exist in `backend/supabase/migrations/` at the
time of this ADR. Every decision below assumes that schema lands as
specified in BRD §5/SYSTEM.md §1; the task breakdown makes the dependency
explicit (`blocked-by:#3`, same pattern ADR-0003/FR-4 used for its query
tool layer) rather than working around it.

Three real design choices fall out of FR-5 that aren't dictated by the BRD:
how to compute "spent" per cap, how to keep the LLM suggestion grounded
(NFR-4: no figure from model recall), and which email provider/cron design
to use for the digest.

## Decision

### 1. Progress aggregation: overall vs. per-category, computed in the backend, not a DB view

- **Overall cap progress**: `spent_paise = SUM(orders.grand_total_paise)`
  for the user, `ordered_at` within the target month, `is_cancelled = false`.
  Uses `grand_total_paise` directly per BRD §2.8 — never recomposed from
  items.
- **Per-category cap progress**: `orders.grand_total_paise` cannot be
  attributed to a category (it's an order-level total that includes fees/
  discounts). Category spend is therefore computed at the item level:
  `SUM(order_items.quantity * order_items.unit_price_paise)` for items whose
  `category` matches, joined through `orders` for the user/month/
  not-cancelled filter. This is an explicit, narrow exception to "trust
  grand_total" — it applies only to category attribution, not to whether an
  order counts at all.
- Both aggregates are computed in Python inside `app/budgets/service.py`
  after two scoped PostgREST `select` calls (order ids + totals for the
  month, then their items) — no new Postgres view or RPC function. `core/db.py`
  is a generic REST wrapper by design (ADR-0002); introducing a DB-side
  aggregate would add a second query surface for the same data with its own
  RLS/testing story for marginal gain at this data volume (<25 users, sync
  windows of days-to-months).
- "Near" a cap (BRD FR-5.2 acceptance criterion) is `spent_paise >= 0.8 *
  cap_paise`, a constant (`BUDGET_NEAR_THRESHOLD_PCT = 80`) in
  `budgets/service.py`, not user-configurable in Phase 1 (no requirement for
  it).
- Category is stored as free text on both `order_items.category` (FR-2) and
  `budgets.category` — no enum. FR-5 does not define or validate a category
  taxonomy; that's FR-2's normalization concern. The budgets cap form takes
  free-text category input rather than a dropdown sourced from real data, to
  avoid FR-5 depending on FR-2's category values existing before FR-5 ships
  its own tasks.

### 2. Cut-suggestions: retrieval-then-generate, not the FR-4 agent

FR-5.2 requires suggestions that reference the user's actual order lines,
and NFR-4 requires every stated number to come from the database. ADR-0003
(FR-4) built a capped LangChain tool-calling agent for open-ended NL
questions — FR-5 doesn't need that generality, since the retrieval query
here is fixed (top order lines driving spend in the over/near-cap category,
or overall, for the target month), not user-composed. Reusing FR-4's
`llm/agent.py`/`llm/tools.py` tool-calling loop for a single fixed query
would add a decision layer (which tool to call, how many hops) that has
only one right answer here.

Design: `budgets/service.py` fetches the top-N (N=10) `order_items` by
`quantity * unit_price_paise` for the relevant scope (category+month, or all
categories if the overall cap is the one over/near), computed in Python —
these rows (name, amount, date, restaurant/product) are the only source of
truth. `llm/budget_suggestions.py` (new, sibling to `llm/agent.py`) calls
`init_chat_model(...)` directly — same construction as `llm/agent.py`
(BRD §3 / SYSTEM.md §1: `LLM_PROVIDER`-driven, currently Groq) — with a
single, non-agentic prompt-completion call, no `bind_tools`/tool loop. The
pre-computed rows are serialized into the prompt; the model is asked only
for prose suggestions that cite the given items by name, never new numbers.
The API response returns the pre-computed rows alongside the generated text
so the frontend renders numbers verbatim from data, not from the LLM's
restatement of them.

### 3. Weekly digest: Resend, one cron trigger, idempotent per user per week

**Provider: Resend** (over SendGrid). Reasons: a plain REST POST
(`api.resend.com/emails`) fits the Worker `fetch`-based backend with no SDK
dependency questions under Pyodide (same constraint that ruled out
`supabase-py` in ADR-0002); free tier (100/day, 3,000/month) comfortably
covers the <25-user friends-beta (NFR-3); SendGrid's free tier is no longer
available to new accounts. Worker secret `RESEND_API_KEY`, added to
`core/config.py` and `.env.example`.

**Cron design**: a second Cron Trigger entry in `wrangler.toml` (weekly,
`0 8 * * 1` — Monday 08:00 UTC, arbitrary and changeable, not a BRD
requirement) alongside whatever daily sync trigger FR-2 adds. Both triggers
invoke the same Worker `scheduled()` handler; the handler dispatches on
`event.cron` to either `sync/cron.py` or the new `digest/cron.py`. Whichever
of FR-2/FR-5 lands first establishes the dispatch-by-`event.cron` pattern in
`main.py`; the second extends it rather than assuming it owns the only
cron. This is called out explicitly in the digest task so the Developer
checks for an existing `scheduled()` handler before adding a new one.

**Idempotency**: `digest/cron.py` iterates distinct `user_id` values from
`budgets` (users with at least one cap set, per BRD FR-5.3), and for each
checks a new `user_digest_state(user_id PK, last_sent_at)` row — skip if
`last_sent_at` falls within the current ISO week, else compute progress via
`budgets/service.py` (the same function the progress API uses), send via
Resend, then upsert `last_sent_at = now()`. This survives a cron retry or a
manual re-run without double-sending. Digest send logging records only
counts and success/failure per NFR-2 (no order data, no email addresses, no
digest content in logs).

**Service-role confinement (extends ADR-0002)**: `digest/cron.py` is
system-initiated with no browser-present user, the same situation as
`sync/` and `oauth/`'s callback handler. It uses the service-role client,
scoping every query explicitly by `user_id` (never by trusting request
input, since there is none). ADR-0002's confinement list ("`sync/` and
`oauth/`; no other module may import the service-role client") is extended
to include `digest/`. The progress-computation function in
`budgets/service.py` itself stays access-mode-agnostic (it takes a
`PostgrestClient` as a parameter) so the same code path runs under
user-JWT-forwarding mode (API route, RLS-enforced) and service-role mode
(digest cron, explicitly scoped) without duplicating the aggregation logic.

## Consequences

- Per-category progress and cut-suggestions are hard-blocked on FR-2
  (issue #3) shipping `orders`/`order_items` — reflected as `blocked-by:#3`
  on the relevant FR-5 task issues, same as ADR-0003/FR-4's `#34`. Cap CRUD
  itself (BRD AC-1, AC-6) is not blocked, since it only touches the new
  `budgets` table.
- `budgets/service.py` becomes the one place category-spend math lives;
  FR-3's dashboard (category breakdown, issue #4) will likely want the same
  per-category aggregation and should import/reuse it rather than
  reimplementing when that feature is architected.
- Two cron triggers on one Worker means `main.py`'s `scheduled()` handler
  must dispatch by `event.cron` from the first cron task onward — a
  coordination point between FR-2 and FR-5 called out in both task sets.
- `llm/budget_suggestions.py` duplicates `llm/agent.py`'s `init_chat_model`
  construction rather than sharing a wrapper — deliberately, since both are
  a few lines and a shared `llm/client.py` would be a premature abstraction
  over two call sites with different needs (one binds tools, one doesn't).
  Revisit only if a third non-agentic LLM call site appears (e.g. FR-6
  calorie mapping).
