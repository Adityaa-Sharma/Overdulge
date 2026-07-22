# ADR-0003: Calorie estimation — grounding, caching, and tone safety

Status: Accepted
Context issue: #7 (FR-6 Calorie estimation & weekly rollups)

## Context

FR-6 asks for an LLM-generated `calorie_estimate` per eligible order item,
mapped against "an Indian nutrition reference," plus a weekly rollup. Three
things need a real decision before this can be split into tasks:

1. NFR-4 says LLM outputs that state numbers must come from the database,
   not model recall — but FR-6.1 explicitly asks the LLM to produce the
   number in the first place. Left unresolved, every calorie task would
   re-litigate what "grounded" means.
2. Recomputing an estimate on every dashboard view means an LLM call per
   item per view — slow, costly, and it lets the same item's displayed
   calories drift between views if the model's answer isn't deterministic.
3. FR-6.3's tone rule (no body/weight/dieting language, ever) is a hard
   product constraint, not a style preference; AC-4 makes QA responsible for
   verifying it, but the code should not rely solely on a human/QA pass
   catching a bad LLM completion before it ships.

## Decision

1. **Reference-grounded prompting, not recall.** A curated static dataset of
   common Indian dishes/packaged items with per-serving kcal
   (`backend/app/llm/data/indian_nutrition_reference.json`) is included in
   the estimation prompt as context every time. The LLM maps the order
   item's name to the closest reference entries and returns a structured,
   validated numeric response; unparseable or out-of-range output (negative,
   or implausibly high for a single food item) is discarded and stored as
   `NULL` rather than guessed. This satisfies NFR-4's intent — the number
   the user sees is either grounded in the reference data or absent, never a
   bare model guess presented as fact.
2. **Estimate once, at ingest; rollups are pure SQL.** `sync/normalize.py`
   calls the estimator once per order item at sync time and persists the
   result to `order_items.calorie_estimate` (BRD §5). The weekly rollup
   endpoint only sums a stored column scoped to the authenticated user — no
   LLM call, and no drift, on read. Re-estimation only happens if an item is
   re-synced (should not normally occur given idempotent upserts on
   `(platform, platform_order_id)`).
3. **Eligibility is a pure function, not a stored flag.** Whether an item
   counts (BRD FR-6.1, AC-1/AC-5) is computed from data already in the
   canonical schema — `platform` and `category` — via
   `llm.calorie_estimator.is_calorie_eligible(platform, category)`: Swiggy
   Food items are always eligible; Instamart/Zepto items are eligible only
   if `category` is on a maintained ready-to-eat allow-list (bakery, snacks,
   beverages, ready-meals). No new `order_items` column is needed beyond
   `calorie_estimate` already in BRD §5. Ineligible items are skipped at
   ingest (`calorie_estimate` stays `NULL`) and therefore excluded from
   rollup sums automatically — no separate filter needed downstream.
4. **Tone safety is enforced in code, not only reviewed in QA.** Any
   LLM-generated commentary string (not the numbers) passes through
   `llm.tone_guard.check_tone()` — a denylist/regex screen for
   body/weight/dieting language — before it is persisted or returned.
   Failing text is dropped in favor of a static playful fallback string, not
   surfaced as-is. QA's AC-4 content check remains the acceptance gate; this
   guard is the code-level backstop so a single bad completion can't reach a
   user between QA passes.

## Consequences

- Calorie features need a shared LangChain chat-model client
  (`llm/client.py`, provider read from `LLM_PROVIDER` per BRD §3/SYSTEM.md
  §1) before any estimation code — scoped as its own scaffolding task since
  FR-4 and FR-5 will need the same client and should not each reimplement
  it.
- The reference dataset is maintained as a versioned JSON file in-repo, not
  a database table — it's read-only grounding context, not user data, and
  keeping it in-repo makes prompt/data changes reviewable in the same PR as
  estimator logic changes.
- `order_items.calorie_estimate` being `NULL` is overloaded to mean both
  "not yet estimated" and "ineligible" — acceptable because nothing outside
  the sync path writes this column, so there is no ambiguity in practice;
  a future task that needs to distinguish the two states will need an
  explicit status column instead.
- All calorie-estimation tasks that touch `order_items` are blocked on FR-2
  (#3, order sync & normalization) landing, since that feature creates the
  `orders`/`order_items` tables and `sync/normalize.py` this design hooks
  into. The estimator, eligibility function, and tone guard themselves have
  no such dependency and can be built and unit-tested against fixture data
  first.
