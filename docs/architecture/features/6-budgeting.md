# FR-5: Budgeting — architecture note

Parent issue: #6
Related: ADR-0007 (this feature's decisions), ADR-0002 (Supabase access
pattern — service-role confinement extended), ADR-0003 (NL query engine
tool-calling — grounding pattern reused), ADR-0006 (Python-side aggregation
— `analytics/aggregate.py` extended)

## 1. Summary

Four new surfaces, no new frontend dependency:

1. `budgets` table (BRD §5) + CRUD routes for caps.
2. `analytics/aggregate.py` gains `budget_progress()` — spend-vs-cap for the
   current month, per BRD §5 categories, reusing the same row-in/dict-out
   pattern ADR-0006 established.
3. `llm/tools.py` (FR-4, #34) gains one grounded tool; a new
   `llm/budget_suggestions.py` generates "where to cut" text for any
   category at or past 80% of its cap, referencing real order lines (AC-3).
4. A new `backend/app/digest/` module + a second Cloudflare Cron Trigger
   send the weekly email digest (AC-4) via Resend (ADR-0007 §3).

No real-time alerts (AC-5) — the digest and in-app progress are the only
two surfaces; nothing pushes a notification outside the weekly cron.

## 2. Schema — `budgets`

```sql
create table public.budgets (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    month date not null,        -- always the first of the month, e.g. 2026-07-01
    category text,              -- NULL = overall cap; matches order_items.category values verbatim
    cap_paise int not null check (cap_paise > 0)
);

create unique index budgets_user_month_category_uq
    on public.budgets (user_id, month, category)
    where category is not null;

create unique index budgets_user_month_overall_uq
    on public.budgets (user_id, month)
    where category is null;
```

Postgres treats `NULL` as distinct from itself in a plain `unique`
constraint, so one overall cap (`category IS NULL`) per `(user_id, month)`
needs its own partial unique index alongside the per-category one — a
single `unique (user_id, month, category)` would silently allow duplicate
overall caps. RLS is the standard four-policy template
(`backend/supabase/README.md`, ADR-0002) — `budgets` is a normal user-scoped
table, no join-back needed (unlike `order_items`).

`category` is freeform text matching whatever `order_items.category` values
ingest populates (BRD §5 — no fixed enum exists yet); a cap for a category
that has no matching spend yet is valid and simply shows 0% progress. No
new validation beyond `cap_paise > 0` — a zero-or-negative cap has no
sensible progress percentage and is rejected at the DB constraint, backstopped
by the route (§3).

## 3. CRUD + progress routes

`backend/app/api/budgets.py`, all user-JWT-forwarding (ADR-0002 — RLS scopes
every call, no explicit `user_id` filter in route code):

- `POST /api/v1/budgets` — create-or-replace a cap for `(month, category)`.
  Upsert on the matching partial-unique index (`on_conflict="user_id,month"`
  when `category` is absent from the body, `on_conflict="user_id,month,category"`
  otherwise) — AC-1, AC-6 ("editable").
- `GET /api/v1/budgets?month=2026-07` — every cap for that month plus
  progress, computed by fetching that month's `orders`/`order_items`
  (same two PostgREST calls the dashboard route makes, ADR-0006) and calling
  `analytics/aggregate.py`'s new `budget_progress()` (§4) — AC-2.
- `DELETE /api/v1/budgets/{id}` — deletes the cap row only; never touches
  `orders`/`order_items` (AC-6's "without deleting historical order data" —
  true by construction, since this route never writes to those tables).

`month` in the query string / body is always normalized to the first of
that calendar month server-side before any DB read/write — the caller never
supplies a mid-month date that could land on the wrong partial-unique-index
bucket.

## 4. `analytics/aggregate.py` addition — `budget_progress`

```python
def budget_progress(
    orders: list[Row],
    order_items: list[Row],
    budgets: list[Row],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    ...
```

Per budget row: `{"category": str | None, "cap_paise": int, "spent_paise": int,
"pct": float, "status": "ok" | "near" | "over"}`. `spent_paise` for a
`category` cap is the matching slice of the same `item_categories_paise`
grouping `category_breakdown()` already computes (`quantity * unit_price_paise`
summed per `order_items.category`); for the overall (`category is None`) cap
it's `spend_totals()`'s `this_month_paise.combined`. `pct = spent_paise /
cap_paise` (cap is DB-constrained `> 0`, so no divide-by-zero guard needed
here, unlike `spend_projection`'s `days_elapsed` case). `status` is `"over"`
at `pct >= 1.0`, `"near"` at `pct >= 0.8` (AC-3's literal threshold),
`"ok"` otherwise. Pure function, no I/O, explicit `now` — same discipline as
every other `aggregate.py` function (ADR-0006), unit-testable with fixture
rows and no PostgREST mock needed.

## 5. Grounded "where to cut" suggestions

Extends ADR-0003's fixed tool-calling set rather than inventing a parallel
LLM pathway — see ADR-0007 §2 for the full design (new tool signature,
prompt shape, and how the existing numeric-grounding guard is reused
unmodified). Surfaced as its own endpoint, `GET
/api/v1/budgets/suggestions?month=2026-07`, called by the frontend only
after `GET /api/v1/budgets` shows at least one `"near"`/`"over"` category —
kept separate from the progress endpoint so a slow LLM round-trip never
blocks the (fast, DB-only) progress numbers from rendering (AC-2's progress
display must not wait on AC-3's suggestions). Returns `{"suggestions": []}`
when no category is at or past 80% — not an error, not an empty-prompt call
to the model.

## 6. Weekly digest

New `backend/app/digest/` module and a second Cloudflare Cron Trigger,
alongside FR-2's daily sync trigger in the same `wrangler.toml`
`[triggers]` array — see ADR-0007 §3 for provider choice (Resend), the
service-role scope extension this requires (ADR-0002 amendment), and the
`Default.scheduled()` dispatch design shared with FR-2's sync cron.

Content per recipient (AC-4): this-week and month-to-date spend
(`spend_totals()`, reused verbatim from `analytics/aggregate.py`) plus
`budget_progress()` for every cap they have set that month — no
LLM-generated text in the digest (unlike §5); a weekly batch job is the
wrong place to add per-user LLM latency/cost when the content is already
fully DB-grounded numbers. Recipients are every distinct `user_id` with at
least one `budgets` row for the current month (AC-4's "each user with at
least one budget cap set").

## 7. Frontend

`frontend/src/routes/Budgets.tsx`: cap-setting form (overall + add/edit/delete
per-category), progress section reusing the dashboard's hand-rolled
progress-bar-style pattern (`frontend/src/components/charts/`, no new
dependency — same reasoning as the dashboard note §5), a suggestions panel
that fetches `/budgets/suggestions` lazily (§5) and renders nothing if the
list is empty. Loading/error/populated/empty states per the existing
convention; INR formatting reuses the dashboard's paise formatter.

## 8. Task breakdown

- **FR-5: `budgets` schema, RLS, CRUD helpers** — migration + `core/budgets.py`.
  Independent of every other FR-5 task — ready for dev immediately.
- **FR-5: `budget_progress` aggregation** — extends `analytics/aggregate.py`
  (#60, already merged). Pure function, contract fully specified in §4 —
  ready for dev immediately, independent of the schema task.
- **FR-5: Budget CRUD + progress API routes** — §3. Blocked by the schema
  task, the aggregation task, and #43 (needs real `orders`/`order_items`
  for integration-shaped tests, same reasoning as #61).
- **FR-5: Grounded cut-suggestion tool + endpoint** — §5/ADR-0007 §2.
  Blocked by #34 (extends `llm/tools.py`, must exist first) and the CRUD +
  progress route task (needs `budget_progress()`'s `status` field to decide
  which categories to ground suggestions for).
- **FR-5: Weekly digest cron + email delivery** — §6/ADR-0007 §3. Blocked
  by the CRUD + progress route task (reuses the same progress computation).
  Not blocked by the suggestions task — digest content is numbers-only.
- **FR-5: Budgets frontend** — §7. Blocked by the CRUD + progress route
  task and the suggestions task.
- Follow-up docs task (not part of FR-5's acceptance criteria): add
  `backend/app/digest/` to `SYSTEM.md` §2's module tree and record ADR-0002's
  service-role confinement extension to include `digest/` (ADR-0007 §3).
