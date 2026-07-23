# ADR-0008: Calorie estimation — reference-grounded prompting, ingest-time caching inside the sync core, category-based eligibility (no schema change), and a code-level tone guard

Status: Accepted
Context issue: #7 (FR-6 Calorie estimation & weekly rollups)

## Context

FR-6 asks for an LLM-generated `calorie_estimate` per eligible order item,
mapped against "an Indian nutrition reference," plus a weekly rollup, with a
hard tone constraint (FR-6.3: playful about ordering habits/money only,
never body/weight/dieting). Four things need a real decision before this can
be split into tasks, and a prior architecture attempt for this issue
(PR #33, closed unmerged on a stale merge conflict before the current
new-files-only Mode A rule existed) had already reasoned through most of
them usefully — this ADR keeps that reasoning, fixes it to the interfaces
ADR-0004 already committed to (`llm/calories.py::estimate_calories`, cited
verbatim by task #41), and fixes the ingest hook to respect ADR-0005's
already-accepted characterization of `sync/normalize.py` as I/O-thin.

1. NFR-4 says LLM outputs that state numbers must come from the database,
   not model recall — but FR-6.1 explicitly asks the LLM to produce the
   number in the first place. Left unresolved, every calorie task would
   re-litigate what "grounded" means here.
2. Recomputing an estimate on every rollup view means an LLM call per item
   per view — slow, costly, and it lets the same item's displayed calories
   drift between views if the model's answer isn't deterministic.
3. BRD §5's canonical schema has no ready-to-eat flag, and `order_items.category`
   has no fixed enum yet (adapters #49/#50/#51 are still unimplemented) — FR-6.1's
   "excluded unless clearly ready-to-eat" needs an eligibility rule that
   doesn't require a schema change or a coordinated edit to three other
   features' still-open task issues.
4. FR-6.3's tone rule is a hard product constraint, not a style preference;
   AC-4 makes QA responsible for verifying it, but the code should not rely
   solely on a human/QA pass catching a bad completion before it ships.

## Decision

### 1. Reference-grounded prompting, not recall
A curated static dataset of common Indian dishes/packaged items with
per-serving kcal, `backend/app/llm/data/indian_nutrition_reference.json`, is
included as context in every estimation prompt. The model maps the order
item's name to the closest reference entries and returns a structured
numeric response; unparseable output, or a value outside
`(0, MAX_PLAUSIBLE_KCAL]` (a generous single-item ceiling, e.g. 5000 kcal, to
catch clearly-broken completions without hand-tuning per-dish), is discarded
and stored as `NULL` rather than guessed. This is what "grounded, not
model recall" means operationally here: the number the user sees is either
traceable to the reference dataset or absent, never a bare unvalidated
guess presented as fact.

### 2. Eligibility is a pure function over existing columns — no schema change
`llm/calories.py::is_calorie_eligible(platform: str, category: str | None) -> bool`:
`swiggy_food` items are always eligible (every Food line is, by definition,
ready-to-eat). `swiggy_instamart`/`zepto` items are eligible only if
`category` (lowercased) is on a small maintained allow-list living next to
this function (`READY_TO_EAT_CATEGORIES` — e.g. snacks, beverages, bakery,
ice-cream/frozen desserts, ready-to-eat). `category is None` (not yet
populated, or a value the allow-list doesn't recognize) is **not**
eligible — the default is exclusion, matching FR-6.1's "unless clearly
ready-to-eat" wording, and it means ineligible items simply never get an
estimate rather than needing a separate flag to say so.

This avoids adding an `order_items` column and avoids touching #43/#50/#51's
scope at all (contrast with FR-3's `vendor_name`, which genuinely needed a
new column because no existing field could carry restaurant identity — here
`category` already carries what's needed once a value exists). The
allow-list is deliberately small and is expected to be extended in place as
the grocery adapters (#50/#51) land and real `category` values become known
— that is a normal follow-up edit to `llm/calories.py`, not an architecture
change.

FR-7's live-suggestion matching (#41, ADR-0004 §4) must apply this same
function to a live search result's platform/category before calling
`estimate_calories` on its name — it does not get to invent its own
eligibility rule. A comment is left on #41 pointing at this function.

### 3. Estimate once, inside the sync core; rollups are pure DB reads
Calling the estimator from `sync/normalize.py` was the prior attempt's
design and is rejected here: ADR-0005 is explicit that `normalize.py` is
"intentionally thin... the only module that talks to `db.py` for order
data" and is platform-agnostic I/O-wise — adding LLM network calls there
contradicts a characterization ADR-0005 already committed and shipped.
Instead, the hook lives one layer up, in `sync/cron.py`'s
`run_sync_for_account` (#52), as a step appended **after** `normalize.upsert_orders`
returns: for each upserted `order_items` row where
`is_calorie_eligible(platform, category)` is true and `calorie_estimate is
null`, group the batch by `lower(trim(name))`, call `estimate_calories`
once per distinct name (not once per row — the same dish reordered five
times in one sync batch is one LLM call, not five), and write the result
back to every matching row via a plain PostgREST update. `normalize.py`
itself is untouched; `upsert_orders`'s payload still never includes
`calorie_estimate`, which is exactly why a value written here is never
clobbered by a later re-sync of the same order.

This is a new `sync/` → `llm/` import that SYSTEM.md's module list doesn't
currently show; it's additive to `run_sync_for_account`'s existing
responsibilities (§4 of ADR-0005), not a rewrite of them, and stays inside
the `sync/` directory ADR-0002 already grants service-role access to — no
service-role confinement-list change needed (contrast with the digest
feature, which genuinely needed a new directory).

Per-distinct-name LLM calls during sync are an accepted N+1-shaped cost at
Phase-1 scale, same tradeoff already accepted twice in ADR-0005 for Food's
per-order detail calls and Zepto's per-order date resolution — revisit only
if sync duration becomes a real problem, not a Phase-1 blocker.

Rollup reads (`GET /api/v1/calories`) only ever sum the already-stored
`order_items.calorie_estimate` column, scoped to the authenticated user
(ADR-0002 user-JWT mode) and read-time-filtered `is_cancelled = false`
(same convention as the dashboard, ADR-0006) — no LLM call, and no drift, on
read.

### 4. Tone safety is enforced in code, not only reviewed in QA
Any LLM-generated commentary string (the optional playful weekly blurb,
§ below — never the calorie/spend numbers themselves) passes through
`llm/tone_guard.py::check_tone(text) -> bool` before it is returned: a
denylist/regex screen for body-, weight-, and dieting-language patterns
(e.g. "lose weight", "calories burned", "diet", "BMI", "overweight", "should
eat less" — a maintained list, extended as QA's content check surfaces
misses). Failing text is replaced with a static playful fallback string
(`llm/tone_guard.py::FALLBACK_BLURB`), never surfaced as-is. QA's AC-4
content check remains the acceptance gate; this guard is the code-level
backstop so a single bad completion can't reach a user between QA passes.

### 5. Weekly blurb: fait-accompli numbers, dual guard reuse
`llm/calories.py::generate_weekly_blurb(*, week_kcal_estimate: int,
week_order_count: int, week_spend_paise: int) -> str` follows ADR-0003's
fait-accompli pattern exactly (same one FR-5's cut-suggestions reuse,
ADR-0007 §1): the model is handed the already-computed numbers as fixed
facts and asked only for one playful sentence about ordering habits/money
referencing them — never asked to compute or restate a number freely. Two
guards run on the output, in sequence: the existing post-generation
numeric-grounding guard from `llm/agent.py` (ADR-0003, imported unmodified —
a third caller of it, after FR-5's suggestions) catches a number that
doesn't match what was handed in; `tone_guard.check_tone()` (§4) catches
body/weight/dieting language regardless of whether the numbers are correct.
Either guard failing falls back to a static sentence — the blurb is always
optional decoration around DB-grounded figures the frontend already renders
independently, never the only place a number appears.

Served from its own lazy endpoint, `GET /api/v1/calories/commentary`, same
reasoning as ADR-0007 §2: an LLM round-trip must never block the (fast,
DB-only) rollup numbers from rendering.

## Consequences

- `llm/calories.py` is a hard dependency for FR-7's #41 (already documented
  in ADR-0004) — this ADR fixes its exact contents
  (`is_calorie_eligible`, `estimate_calories`, `generate_weekly_blurb`) so
  #41 has a concrete interface to import once this feature's estimator task
  lands, not just a filename.
- Every calorie-estimation task that constructs a chat model needs
  `core/config.py`'s `llm_provider`/`groq_api_key` fields, which FR-4's #36
  adds — same reuse-not-reimplement posture FR-5's #70 already took for the
  numeric-grounding guard. `llm/calories.py` constructs its own
  `init_chat_model` call from that config (no shared `llm/client.py` — #36's
  already-written scope builds the call directly inside `llm/agent.py`
  rather than a separate wrapper, and inventing one now would contradict
  that committed scope for a one-more-caller savings that doesn't justify a
  new shared module yet).
- The ingest-time hook depends on `sync/cron.py::run_sync_for_account`
  (#52) existing, which itself depends on both grocery adapters landing —
  the calorie estimator and eligibility function themselves have no such
  dependency and can be built and unit-tested against fixture rows first
  (mirroring how FR-3's aggregation core shipped independently of #43's
  code landing).
- `order_items.calorie_estimate` being `NULL` is overloaded to mean both
  "not yet estimated" and "ineligible" — acceptable because nothing outside
  the sync hook writes this column, so there's no ambiguity in practice; a
  future task that needs to distinguish the two states would need an
  explicit status column instead of overloading NULL further.
- `READY_TO_EAT_CATEGORIES` is a living allow-list, not a one-time schema
  decision — it is expected to be edited in the same repo as the grocery
  adapters land and real `category` strings become known, and that edit
  does not require another ADR.
