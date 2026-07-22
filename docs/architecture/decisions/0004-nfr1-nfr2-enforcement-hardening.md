# ADR-0004: NFR-1/NFR-2 enforcement hardening — denylist, RLS isolation test harness, log redaction

Status: Accepted
Context issue: #9 (Read-only safety guarantee & row-level data isolation verification)

## Context

BRD NFR-1 requires a CI static check blocking any mutating platform tool
call; NFR-2 requires RLS on every user table, a passing cross-user access
test, and zero order/token/PII data in logs (BRD §6, §8 sign-off checklist).

Some of this already exists from earlier scaffolding:
- `.github/workflows/ci.yml` already greps `backend/` for a denylist of
  mutating MCP tool names and fails the build on a match (from #11).
- `linked_accounts`, `oauth_pending_links`, `oauth_clients` already ship
  with RLS enabled and `auth.uid() = user_id` policies per the template in
  `backend/supabase/README.md` (ADR-0002, demonstrated end-to-end in #13).

What's missing is durability and coverage:
- The denylist lives as an inline regex in a workflow YAML file — easy to
  edit accidentally, no test proves it actually catches the tool names it
  claims to, and there's no documented trigger for re-reviewing it.
- No automated cross-user access test exists yet (BRD AC-4) — the #13 demo
  proved the RLS *pattern* works and then reverted the demo table; nothing
  regresses it going forward.
- `orders`, `order_items`, `budgets` (the other three tables BRD AC-3 names)
  don't exist yet — they're owned by FR-2 (#3) and FR-5 (#6), both still
  `needs-architecture`. This issue cannot create them without reaching into
  those features' scope.
- No logging convention or guard exists yet. Nothing currently logs
  (`grep` for `logging`/`print` in `backend/app` is empty), but OAuth (#17,
  in progress) and sync (#3) are about to start writing log lines that will
  have tokens and order payloads in scope.

## Decision

### 1. Denylist becomes a versioned, tested artifact
The mutating-tool denylist moves out of the inline `ci.yml` regex into a
checked-in source of truth: `backend/app/mcp/DENYLIST.md`, listing every
denylisted tool name grouped by platform (Swiggy Food, Instamart, Zepto)
with a one-line note on where it was confirmed (probe script / platform
docs) and the BRD §2 fact it maps to. A small script
(`backend/scripts/check_readonly_guard.py`) parses this file and scans
`backend/` for matches (word-boundary, same semantics as today's regex,
`tests/contracts/` exempted); `ci.yml`'s NFR-1 step calls the script instead
of embedding the pattern. A unit test
(`backend/tests/unit/test_readonly_guard.py`) feeds the script fixture
snippets containing every denylisted name (asserts fail) and a sample of
known read-only tool names (asserts pass), so the guard's own coverage is
regression-tested, not just its callers' code.

`DENYLIST.md` carries a header instruction: review this list whenever BRD
§2 platform facts are revisited (new platform, new probe run, or a platform
API change) — the same trigger already implied by BRD AC-2, now with a
concrete file to edit and a test that fails if an entry is silently
removed without the corresponding tool actually disappearing.

### 2. A reusable RLS cross-user isolation test harness, applied where data exists today
`backend/tests/integration/test_rls_isolation.py` establishes the harness:
spin up the disposable `postgres:16-alpine` container with the stub
`auth.uid()` GUC and non-superuser `authenticated` role (same setup proven
manually in #13's README demo, now codified as a pytest fixture so it runs
in CI rather than living only as a one-time manual verification), seed two
users, and assert cross-user reads/writes return zero rows / are rejected.
Applied now to `linked_accounts` — the only user-scoped table that exists.

This harness is the required pattern, not a one-off: `backend/supabase/README.md`
gets a new section stating that any future migration adding a user-scoped
table (`orders`, `order_items`, `budgets`, and anything after) must add its
own case to `test_rls_isolation.py` in the same PR that creates the table,
exactly as it already must add RLS policies in the same migration (existing
ADR-0002 convention). BRD AC-3/AC-4 coverage for `orders`, `order_items`,
and `budgets` is therefore satisfied incrementally by FR-2's (#3) and
FR-5's (#6) own task breakdowns, not duplicated here — those future SA
Mode A runs must carry this requirement into every task that creates a
user-scoped table.

### 3. Logging convention + static backstop
`backend/app/core/logging.py` provides the only sanctioned logging entry
point: a thin wrapper over stdlib `logging` with a structured (JSON) output
and a fixed set of field names. No module may call `logging`/`print`
directly — enforced by extending `check_readonly_guard.py`'s script into a
second check (or a sibling script sharing its scanning helper) that flags
raw `logging.`/`print(` calls outside `core/logging.py` itself, plus a
heuristic flag on any log call whose arguments reference known-sensitive
names (`tokens_encrypted`, `access_token`, `refresh_token`, `code_verifier`,
`raw`). This is a static backstop, not a substitute for review — same
posture as the NFR-1 guard (SYSTEM.md §5). It gives QA's weekly
log-content regression check (BRD AC-5, agents/qa.md Mode B) a concrete,
structured log format to grep instead of unstructured free-text lines.

## Consequences

- The NFR-1 guard's behavior is now covered by its own test, so a future
  edit to the denylist (adding/removing a tool name) that breaks detection
  fails CI immediately instead of silently degrading coverage.
- `orders`/`order_items`/`budgets` RLS + cross-user test coverage is
  explicitly deferred to FR-2 and FR-5's task breakdowns; this ADR is the
  reference those future breakdowns must cite so the obligation isn't
  dropped. #9 tracks this via a checklist referencing #3 and #6 rather than
  closing prematurely.
- Any code that logs before `core/logging.py` exists (none does today) has
  no grandfathering concern; #17 (OAuth engine, in progress) and future
  sync work should adopt the wrapper as their first log call rather than
  reaching for stdlib `logging`/`print` directly.
- Three independent, unblocked PR-sized tasks come out of this ADR (denylist
  hardening, RLS harness, logging convention+guard) — none touches the
  other's files, so they can ship in any order or in parallel.
