# ADR-0007: Budget grounded cut-suggestions (ADR-0003 extension) and weekly digest delivery (ADR-0002 extension)

Status: Accepted
Context issue: #6 (FR-5 Budgeting)

## Context

FR-5.2 requires LLM "where to cut" suggestions that reference the user's
actual order lines, not generic advice. FR-5.3 requires a weekly email
digest to every user with a budget cap set, sent by a Resend/SendGrid-class
provider chosen by the SA. Both need to extend decisions two earlier ADRs
already made narrowly on purpose:

- ADR-0003 fixed the NL query engine's tool set to five functions and was
  explicit that widening it is "a deliberate SA-reviewed decision per
  question class actually seen in use, not a default reflex."
- ADR-0002 confined service-role (RLS-bypassing) Supabase access to exactly
  two directories, `sync/` and `oauth/`, specifically because both are
  system-initiated code paths with no browser-present user JWT to forward,
  and because narrowing the service-role blast radius to two greppable
  directories keeps the NFR-2 cross-user-isolation audit small.

## Decision

### 1. Cut-suggestions: one new tool, not a new LLM pathway

`llm/tools.py` (#34) gains one addition, following the existing signature
style exactly:

```python
def top_items_in_category(
    jwt, start_date, end_date, category=None, limit=5
) -> list[dict]:  # [{name, order_count, total_paise}]
```

`category=None` reuses the existing (uncategorized/overall) `top_items`
behavior; a category value narrows to `order_items.category = :category`.
This is additive to the existing tool, not a parallel query path — same
`is_cancelled=false` default, same "never recompute from components" rule
(BRD §2.8) as every other tool in the set.

`llm/budget_suggestions.py` is new: for each `budget_progress()` row with
`status in {"near", "over"}` (feature note §4), it calls
`top_items_in_category` for that row's category (or `None` for the overall
cap), then builds the same kind of fait-accompli prompt ADR-0003 uses for
the query engine — the model is handed the already-computed cap, spend,
percentage, and item list as fixed facts and asked only to write 2-3
sentences of suggestion text referencing them; it is never asked to
compute or restate a number on its own. **The existing post-generation
numeric-grounding guard from `llm/agent.py` (ADR-0003) is imported and
reused unmodified** — same rejection/regenerate-once behavior if the
model's prose contains a number that doesn't match a code-computed figure.
This is a second caller of that guard, not a second implementation of it.

Widening the tool set by exactly one function, reusing the existing guard,
keeps this consistent with ADR-0003's "reviewed per question class" bar —
"where should I cut back in category X" is one clearly-scoped new class,
not a generalization of the agent's capability.

### 2. Suggestions are a separate, lazily-called endpoint

`GET /api/v1/budgets/suggestions` is its own route, not folded into `GET
/api/v1/budgets`. An LLM round-trip is one to a few seconds; the progress
numbers in `GET /api/v1/budgets` are pure DB reads that should render
immediately regardless of LLM latency or a provider hiccup. The frontend
calls `/suggestions` only once progress data shows a `"near"`/`"over"`
category, and a suggestions-endpoint failure degrades to "no suggestions
shown" without touching the progress display at all.

### 3. Weekly digest: Resend, and a third service-role directory

**Provider: Resend.** Both Resend and SendGrid fit "provider chosen by SA
(Resend/SendGrid class)" per BRD §5.3. Resend is picked because its entire
API surface is one `POST https://api.resend.com/emails` call with a bearer
API key and a JSON body — reachable with the same plain-`httpx` convention
`core/db.py` already established for PostgREST (BRD §3: "do not assume
`supabase-py` works under Pyodide," which generalizes to "assume no
provider SDK's native/non-pure-Python bindings work under Pyodide either").
SendGrid's Python SDK carries the same Pyodide-compatibility risk `db.py`'s
docstring warns about for `supabase-py`, and its API requires sender
identity/domain verification steps beyond a single API key. For a <25-user
friends-beta, Resend's free tier (3,000 emails/month, 100/day) is not a
practical constraint at a weekly cadence. `RESEND_API_KEY` and
`DIGEST_FROM_EMAIL` are added to `core/config.py` by the digest task, read
the same way every other secret is (Worker secrets/env only, BRD §3).

**Service-role scope, extended to `digest/`.** The digest cron has no
browser-present user — same shape as `sync/cron.py` — and needs to (a)
enumerate every distinct `user_id` with a current-month `budgets` row
across *all* users, which RLS-scoped user-JWT mode cannot do, and (b) fetch
each of those users' `orders`/`order_items`/`budgets` to compute their
digest content. This ADR extends ADR-0002's confinement list from
`{sync/, oauth/}` to `{sync/, oauth/, digest/}`. The same discipline
applies: every service-role query in `digest/` is explicitly scoped with a
`user_id = :id` filter (the id comes from the trusted `budgets`/enumeration
query itself, never from external input), so RLS isn't the safety
mechanism there either — same argument ADR-0002 already made for `sync/`
and `oauth/`. `backend/supabase/README.md`'s "import only from `sync/` and
`oauth/`" line and the NFR-2 cross-user-isolation audit scope both need
updating to add `digest/` — done by the digest task itself (a normal code
change, not an architecture-note edit) and recorded in the follow-up docs
task that also updates `SYSTEM.md` §2 (feature note §8).

**Recipient resolution.** The canonical schema (BRD §5) has no email
column on any user-scoped table — email lives only in Supabase Auth.
`digest/send.py` resolves `user_id -> email` via Supabase's GoTrue Admin
API (`GET /auth/v1/admin/users/{user_id}`, service-role key), one call per
recipient — acceptable at <25-user scale, and it avoids inventing a
`profiles` mirror table just to carry an email address the auth system
already owns.

**Cron dispatch.** `worker.py`'s `Default` class currently implements only
`fetch` (no `scheduled` handler exists yet — FR-2's sync orchestration task,
#52, is the other feature that needs one). Whichever of the two sync-cron
task and this digest task lands first adds `Default.scheduled(self,
controller)`, dispatching on `controller.cron` to its own handler; the
second task extends the existing dispatch with an additional `elif`
branch rather than replacing it. `wrangler.toml`'s `[triggers]` `crons`
array gains the weekly schedule (e.g. `"0 3 * * 1"`, Monday 03:00 UTC — an
hour that doesn't collide with the daily sync's own trigger time, whichever
that ends up being) alongside the daily one, additive either order.

## Consequences

- Two features (FR-2 sync, FR-5 digest) both touch `worker.py`'s
  `scheduled` dispatch and `wrangler.toml`'s `crons` array — the digest
  task must check current state of both files before editing, and add
  rather than replace, exactly as the dashboard feature note (#4 §4) had
  to coordinate with #43/#49 on shared scope.
- The NFR-2 service-role audit surface grows from two directories to three;
  still fully greppable, still each call site individually scoped.
- If a future feature needs its own batch/system job with no user JWT, the
  same extension pattern applies: a new ADR amending the confinement list,
  not a silent import from a fourth directory.
- Suggestion quality depends on `order_items.category` being populated at
  ingest (FR-2); a category with no `order_items.category` data yet still
  gets a valid `budget_progress()` row (0% spent) but an empty
  `top_items_in_category` result — `budget_suggestions.py` must skip
  generating a suggestion when the grounding list is empty rather than
  asking the model to write one with nothing to reference.
