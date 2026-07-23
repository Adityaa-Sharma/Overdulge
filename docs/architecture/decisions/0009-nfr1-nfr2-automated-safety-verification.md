# ADR-0009: NFR-1/NFR-2 automated safety verification — denylist consolidation, structured log redaction, RLS isolation harness

Status: Accepted
Context issue: #9 (Read-only safety guarantee & row-level data isolation verification)

## Context

BRD §6 NFR-1/NFR-2 and §7 R-1 make two guarantees non-negotiable: Overdulge
never calls a mutating platform tool (an accidental Swiggy order is
non-cancellable COD — a critical incident, BRD §2.10), and no user can ever
read another user's data. Both already have partial coverage, discovered
while scoping #9:

1. **NFR-1** has a working CI guard today (`.github/workflows/ci.yml`, "NFR-1
   read-only guard" step) — an inline bash regex denylist. It works, but it
   is not "documented" in the sense AC-2 asks for: the list lives only as an
   unannotated regex inside a YAML `run:` block, untested, with no mapping
   from tool name to the BRD §2 platform fact that names it, and no
   instruction to revisit it when platform facts change.
2. **NFR-2** RLS policies exist on `linked_accounts` (migration `0003`) and
   the four-policy template is documented in `backend/supabase/README.md`.
   But the *only* proof this template actually works against a real Postgres
   was a one-time manual local run during scaffolding (`0001`/`0002`, the
   demo table pair) — never automated, never repeated, and the demo table
   was dropped afterward. Every current data-access test
   (`tests/unit/test_linked_accounts.py`) mocks `httpx` and never touches a
   real database, so it cannot catch a missing or wrong `USING` clause.
   `orders`/`order_items`/`budgets` don't exist yet (#43, #67 still open).
3. **No logging exists yet anywhere in `backend/app`** (verified — zero
   `logging`/`print` call sites at time of writing). That's a rare
   opportunity: the safety net can go in *before* the first log line, which
   is cheaper than auditing log call sites after the fact once FR-2 sync
   error handling and FR-5's digest inevitably add them.

## Decision

### 1. Denylist: consolidate into one documented, tested source

`backend/app/core/nfr1_denylist.py` — a plain data module (not imported by
any request-handling code path, just a constant + docstring):

```python
# Reviewed whenever BRD §2 platform facts are revisited (BRD §6 NFR-1 AC-2).
# Each entry: mutating MCP tool name -> the platform/category it belongs to.
MUTATING_TOOL_DENYLIST: dict[str, str] = {
    "place_food_order": "swiggy_food",
    "confirm_order": "swiggy_food",
    "update_food_cart": "swiggy_food",
    "flush_food_cart": "swiggy_food",
    "apply_food_coupon": "swiggy_food",
    "checkout": "swiggy_instamart",
    "update_cart": "swiggy_instamart",
    "clear_cart": "swiggy_instamart",
    "create_order": "zepto",
    "create_online_payment_order": "payment",
    "create_upi_reserve_pay_order": "payment",
    "create_wallet_order": "payment",
    "add_saved_address": "account_mutation",
    "update_drop_zone": "account_mutation",
    "update_user_name": "account_mutation",
}
```

`backend/scripts/check_nfr1_denylist.py` builds the same `\b(...)\b` regex
from this dict's keys and greps `backend/` (excluding
`tests/contracts/`), replacing the inline bash regex in `ci.yml` — the CI
step becomes `uv run python scripts/check_nfr1_denylist.py`. A unit test
(`tests/unit/test_nfr1_denylist.py`) asserts the built regex flags a fixture
string containing each denylisted name and does not flag an unrelated
identifier containing one as a substring (the existing `\b` word-boundary
reasoning, now under test instead of only a code comment). Adding a newly
learned mutating tool name is now a one-line dict entry with a mandatory
platform tag, not an edit to a YAML regex.

### 2. Log safety: allowlist, not denylist, enforced by a single wrapper

NFR-2's "no order data, tokens, or PII in logs" is an open-ended set of
sensitive shapes (order lines, item names, amounts, tokens, emails) — unlike
the mutating-tool list, which is closed and fully known. A denylist of
"forbidden" log fields would silently miss the next sensitive field someone
adds. Going the other way — **only allowlisted keys may be logged** — fails
safe: a new field is invisible until someone deliberately allowlists it.

`backend/app/core/safe_log.py`:

```python
_ALLOWED_FIELDS = {"user_id", "platform", "route", "status_code",
                    "duration_ms", "error_type", "sync_state_key"}

def log_event(level: str, message: str, **fields: Any) -> None: ...
```

Wraps stdlib `logging` internally; rejects (raises, not silently drops —
silent dropping would hide the bug at review time) any keyword argument
whose key isn't in `_ALLOWED_FIELDS`, and `message` itself must be a static
string literal at call sites (enforced by convention + code review, not
mechanically — f-strings interpolating request data into `message` defeat
the allowlist). `user_id` is a UUID, not PII by itself and already the RLS
join key everywhere else in the codebase, so it's allowed for
correlating sync failures to an account without naming what was in it.

CI guard (new step in the existing NFR-1 guard job, same "greppable
enforcement" pattern): `grep -rnE '\b(import logging|logging\.(debug|info|
warning|error|exception|critical)\()\b' backend/app --include='*.py'` fails
the build on any hit outside `core/safe_log.py` itself. This makes "all
logging goes through the redaction allowlist" structural, not
review-dependent, before the first real log line is written by FR-2/FR-5.

QA's weekly regression (agents/qa.md Mode B) adds a runtime spot-check as
defense-in-depth once sync is live: grep actual Worker log output for a
canary substring from a known seeded order (e.g. its `grand_total_paise`
value or a token ciphertext prefix) and confirm zero matches — the static
guard should make this always pass, but BRD AC-5 asks for a log-content
check specifically, so QA keeps a runtime one too.

### 3. RLS isolation: ephemeral real Postgres in CI, not mocks

Mocked PostgREST responses (the existing unit-test pattern) can never prove
RLS — the mock doesn't run Postgres. Proving AC-3/AC-4 needs an actual
Postgres instance evaluating the real policies from the real migration
files under the real `authenticated` role — the same setup the `0001`/`0002`
demo pair validated manually once.

`tests/integration/test_rls_isolation.py`, gated behind a new CI job
(`backend-integration`) that adds a `postgres:16-alpine` service container
to the existing backend job:

1. A fixture SQL file (`tests/integration/fixtures/auth_stub.sql`) creates
   the `auth` schema + `auth.uid()` function reading the
   `request.jwt.claim.sub` GUC (mirrors Supabase's real implementation,
   exactly as the `0001` demo verified locally) and a non-superuser
   `authenticated` role — RLS is bypassed entirely for a table-owning
   superuser, so the test must connect as `authenticated` or the whole
   suite is a false pass.
2. Every file in `backend/supabase/migrations/*.sql` applies in order via
   `psql` (same command `backend/supabase/README.md` documents) against the
   ephemeral database — this is the actual deployed schema, not a
   hand-copied subset that can drift from it.
3. A small table registry drives the test parametrization:
   ```python
   RLS_TABLES = [
       {"table": "linked_accounts", "user_id_column": "user_id"},
       # orders, order_items (join-back via orders.user_id), budgets appended
       # once #43 / #67 land — see task breakdown below.
   ]
   ```
   For each direct `user_id_column` table: seed one row for user A and one
   for user B (via the service-role/superuser connection, bypassing RLS for
   setup only), then connect as `authenticated` with
   `SET LOCAL request.jwt.claim.sub = '<A's uuid>'` and assert a `select *`
   returns only A's row, an `insert` with `user_id` set to B's id is
   rejected (`new row violates row-level security policy`), and a direct
   `select ... where id = '<B's row id>'` returns zero rows (AC-4's literal
   scenario). `order_items` (no `user_id` column, ADR-0002's join-back
   policy) gets its own case shape once #43 lands: seed via `orders`, assert
   the same three properties through the join.
4. `psycopg[binary]` added to the `dev` dependency group (integration-test
   only, never imported by `app/` — PostgREST-over-HTTP remains the only
   runtime DB access path per ADR-0002, this is purely a test-time direct
   connection).

This is the same test module QA's weekly regression runs (agents/qa.md
Mode B already runs "the full test suite"); no separate QA-only harness
needed.

## Consequences

- The first task is entirely additive/tooling — no existing behavior
  changes, safe to ship independent of every other open FR-2/FR-5 task.
- The logging wrapper landing before any real log call site means the CI
  guard, not a retroactive audit, is what keeps FR-2/FR-5 logging safe —
  consistent with SA rule "scaffolding precedes features."
- The isolation harness only covers `linked_accounts` until #43 (orders/
  order_items) and #67 (budgets) land; a follow-up task (blocked on both)
  extends the table registry rather than re-deriving the harness. Until
  that follow-up merges, AC-4 is proven for one of the four named tables —
  the parent issue stays open with the remaining tables tracked as blocked
  child tasks, not silently declared done.
- `backend-integration` is a second CI job so a Postgres service-container
  cold start doesn't slow down the existing fast unit-test job on every
  push.
