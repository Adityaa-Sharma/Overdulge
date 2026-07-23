# FR-6: Calorie estimation & weekly rollups — architecture note

Parent issue: #7
Related: ADR-0008 (this feature's decisions), ADR-0002 (Supabase access
pattern — `sync/`'s existing service-role scope, no extension needed),
ADR-0003 (numeric-grounding guard reused for the weekly blurb), ADR-0005
(sync core the ingest hook attaches to), ADR-0006 (Python-side aggregation
pattern extended), ADR-0004/#41 (FR-7's live-suggestion matching, the other
caller of `llm/calories.py::estimate_calories`)

## 1. Summary

No new tables, no new columns. Three new files
(`llm/calories.py`, `llm/tone_guard.py`, `llm/data/indian_nutrition_reference.json`),
an additive step inside the existing sync core (`sync/cron.py::run_sync_for_account`,
#52), two additions to `analytics/aggregate.py`, one new API module
(`api/calories.py`, two routes), and one new frontend route. See ADR-0008
for why eligibility needs no schema change and why the estimation hook
lives in `sync/cron.py` rather than `sync/normalize.py`.

## 2. `llm/calories.py` — eligibility + estimation + blurb

```python
READY_TO_EAT_CATEGORIES: set[str]  # lowercase category strings, maintained in-place

def is_calorie_eligible(platform: str, category: str | None) -> bool: ...

def estimate_calories(item_name: str) -> int | None:
    # grounded against llm/data/indian_nutrition_reference.json (ADR-0008 §1)
    # returns None on unparseable or out-of-range (0, MAX_PLAUSIBLE_KCAL] output
    ...

def generate_weekly_blurb(
    *, week_kcal_estimate: int, week_order_count: int, week_spend_paise: int
) -> str:
    # fait-accompli prompt (ADR-0003 pattern); runs the numeric-grounding
    # guard (llm/agent.py, ADR-0003) then llm/tone_guard.check_tone()
    # (ADR-0008 §4); falls back to tone_guard.FALLBACK_BLURB if either fails
    ...
```

`is_calorie_eligible`: `platform == "swiggy_food"` is always `True`.
`swiggy_instamart`/`zepto` are `True` only if `category` (lowercased) is in
`READY_TO_EAT_CATEGORIES`; `None` or an unrecognized value is `False` — the
default is exclusion (FR-6.1's "unless clearly ready-to-eat"). This is the
same function FR-7's #41 must call before estimating a live search
result's calories (ADR-0008 §2) — do not duplicate the allow-list there.

`estimate_calories` takes just an item name (no platform/category) so it
works identically for an already-synced `order_items.name` (this feature)
and an ephemeral live `search_products`/`search_menu` result name (#41,
ADR-0004) — the caller decides eligibility before calling it, this function
only maps a name to a number or `None`.

## 3. `llm/tone_guard.py` — tone-safety backstop

```python
def check_tone(text: str) -> bool: ...  # True = safe to show as-is
FALLBACK_BLURB: str  # static, already tone-safe, used when check_tone fails
```

A denylist/regex screen for body-, weight-, and dieting-language patterns.
Independent of every other FR-6 task — pure function, fixed word list, no
LLM call itself, ready for dev immediately.

## 4. Ingest-time hook — `sync/cron.py::run_sync_for_account`

After `normalize.upsert_orders(...)` returns for a sub-platform (#43, #52):
for the just-upserted `order_items` rows where `is_calorie_eligible(platform,
category)` is true and `calorie_estimate is null`, group by
`lower(trim(name))`, call `estimate_calories` once per distinct name, and
write the result back to every matching row (plain PostgREST update, no
arithmetic, no re-touch of any other column). `normalize.py` itself is not
modified — this step lives in `cron.py`, after normalize's upsert, per
ADR-0008 §3.

Cancelled orders' items are still estimated and stored (storage is not a
read-time concern) — exclusion from any calorie total happens at read time
in §5, mirroring how `is_cancelled` exclusion works for spend (ADR-0005 §5).

## 5. `analytics/aggregate.py` additions

```python
def calorie_totals(
    orders: list[Row], order_items: list[Row], *, now: datetime
) -> dict[str, int]: ...
    # {"this_week_estimate_kcal": int, "this_month_estimate_kcal": int}

def calorie_trend(
    orders: list[Row], order_items: list[Row], *, now: datetime, lookback: int = 12
) -> list[dict[str, Any]]: ...
    # [{"period_start": "2026-07-14", "estimate_kcal": int}], weekly buckets only
```

Same shape/discipline as `spend_totals`/`spend_trend` (ADR-0006): pure, rows
in, explicit `now`, no I/O. Both sum `order_items.calorie_estimate` (skipping
`None`) for items whose parent order's `ordered_at` falls in the bucket;
callers pass already `is_cancelled = false`-filtered rows, same convention
the dashboard route uses. Independent of every other FR-6 task — ready for
dev immediately, same reasoning as FR-3's aggregation-core task and FR-5's
`budget_progress` task.

## 6. Routes — `backend/app/api/calories.py`

Both user-JWT-forwarding (ADR-0002), no service-role.

- `GET /api/v1/calories` — fetches the user's `orders`/`order_items`
  (`is_cancelled = eq.false`, same two PostgREST calls the dashboard route
  makes) and calls `calorie_totals`/`calorie_trend`. Response:
  ```json
  {
    "generated_at": "2026-07-23T10:00:00Z",
    "has_data": true,
    "totals": {"this_week_estimate_kcal": 0, "this_month_estimate_kcal": 0},
    "trend": {"weekly": [{"period_start": "2026-07-14", "estimate_kcal": 0}]}
  }
  ```
  `has_data` follows the dashboard's convention (§ dashboard note §2): a
  separate unfiltered count of the user's orders, not the filtered list —
  a user with only cancelled orders still gets the populated-but-zero shape,
  not the empty state. Every field defaults to `0`/`[]` on no data, same
  no-special-casing discipline as the dashboard response.
- `GET /api/v1/calories/commentary` — lazy, called by the frontend only
  after the rollup above renders (ADR-0008 §5's latency isolation, same as
  FR-5's suggestions endpoint). Fetches this week's `orders`/`order_items`
  again (or reuses the same figures if the frontend already has them —
  route stays simple and re-fetches, matching how `/budgets/suggestions`
  doesn't share a request context with `/budgets`), computes
  `week_order_count`/`week_spend_paise` via the existing `spend_totals`
  aggregate and `week_kcal_estimate` via `calorie_totals`, then calls
  `generate_weekly_blurb`. Response: `{"blurb": "string"}`. Never errors
  the caller into a broken state — a guard-triggered fallback still returns
  a normal 200 with `tone_guard.FALLBACK_BLURB`.

## 7. Estimate labeling (FR-6.3, AC-3)

Every calorie figure in the UI carries a visible "estimate" marker — a
shared `frontend/src/components/EstimateBadge.tsx` (or a `~` prefix
formatting helper next to the existing paise formatter) wraps every kcal
number rendered from this feature's two endpoints. This is a frontend
rendering convention, not a backend field: the whole endpoint is estimates
by construction, so the API contract doesn't need a redundant
`is_estimate: true` on every number.

## 8. Frontend

`frontend/src/routes/Calories.tsx`: one `apiFetch('/calories')` call on
mount for totals + trend (loading/error/populated/empty states per §6's
`has_data`, same convention as Dashboard/Budgets), trend rendered with the
existing hand-rolled chart components (`frontend/src/components/charts/`,
no new dependency — same reasoning as the dashboard and budgets notes), then
a lazy `apiFetch('/calories/commentary')` call rendering the blurb once it
resolves, never blocking the totals/trend render.

## 9. Task breakdown

- **FR-6: `llm/calories.py` — eligibility + reference-grounded `estimate_calories`**
  (§2, ADR-0008 §1–§2) — includes shipping `llm/data/indian_nutrition_reference.json`.
  Blocked by #36 (needs `init_chat_model`/Groq config to exist). Unit-testable
  against a mocked chat model and fixture platform/category pairs.
- **FR-6: `llm/tone_guard.py` — tone-safety guard** (§3, ADR-0008 §4).
  Independent — ready for dev immediately.
- **FR-6: `calorie_totals`/`calorie_trend` aggregation** (§5, ADR-0006 pattern).
  Independent — ready for dev immediately.
- **FR-6: Ingest-time estimation hook in `run_sync_for_account`** (§4,
  ADR-0008 §3). Blocked by #52 (the function being extended) and the
  `llm/calories.py` task above.
- **FR-6: `generate_weekly_blurb` + both API routes** (§6, ADR-0008 §5).
  Blocked by the `llm/calories.py` task, the tone-guard task, the
  aggregation task, and #36 (imports `llm/agent.py`'s numeric-grounding
  guard — must exist first, same dependency #70 already has).
- **FR-6: Calorie rollup frontend** (§7–§8). Blocked by the routes task.
- Follow-up docs task (not part of FR-6's acceptance criteria): add
  `backend/app/llm/calories.py`, `llm/tone_guard.py`, and `llm/data/` to
  `SYSTEM.md` §2's module tree (ADR-0008).
