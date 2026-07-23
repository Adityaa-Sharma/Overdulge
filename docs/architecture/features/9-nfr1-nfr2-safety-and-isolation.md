# Read-only safety guarantee (NFR-1) & row-level isolation verification (NFR-2) — architecture note

Parent issue: #9
Related: ADR-0009 (this issue's decisions), ADR-0002 (Supabase access
pattern / RLS convention this issue verifies), ADR-0001 (token encryption,
also part of NFR-2's scope)

## 1. Summary

Issue #9 is a verification feature: nothing here is user-facing. Three
independent pieces of safety tooling, each landing as its own task:

1. **Denylist consolidation** — the mutating-tool CI guard already exists
   and works; this makes it a documented, tested, single-source module
   instead of an inline YAML regex (AC-1, AC-2).
2. **Structured log redaction** — a wrapper + CI guard that makes it
   structurally impossible to log an unallowlisted field, landing before
   FR-2/FR-5 add the first real log call site (AC-5).
3. **RLS isolation harness** — a real ephemeral-Postgres CI job that proves
   the RLS policies in the actual migration files enforce cross-user
   isolation, starting with `linked_accounts` (the only user-scoped table
   that exists today) and extended once `orders`/`order_items` (#43) and
   `budgets` (#67) land (AC-3, AC-4).

None of these touch `SYSTEM.md` in this PR (SA Mode A rule) — the module
tree additions (`core/nfr1_denylist.py`, `core/safe_log.py`,
`scripts/check_nfr1_denylist.py`, `tests/integration/test_rls_isolation.py`)
get folded into `SYSTEM.md` §2 by a follow-up docs task, same pattern as
#63/#73/#84 for other features.

Full design and rationale for all three: ADR-0009.

## 2. Acceptance criteria mapping

| BRD AC | Covered by |
|---|---|
| AC-1 (CI static denylist check) | Task 1 — already true today via the existing inline guard; task 1 makes it documented/tested per AC-2 without changing its enforcement behavior. |
| AC-2 (denylist documented, reviewed on BRD §2 changes) | Task 1 — `core/nfr1_denylist.py`'s per-entry platform tag + header comment. |
| AC-3 (RLS on all user-scoped tables) | Already true for `linked_accounts` (#16, merged). `orders`/`order_items`/`budgets` get RLS in the same migration that creates them, per the `backend/supabase/README.md` template and ADR-0002 — enforced by task 3/4's harness as each table lands, not re-specified here. |
| AC-4 (cross-user access test, CI/QA regression) | Task 3 (`linked_accounts`) + task 4 (`orders`/`order_items`/`budgets`, blocked on #43/#67). |
| AC-5 (no order data/tokens/PII in logs, log-content check) | Task 2 (structural prevention) + QA Mode B runtime spot-check noted in ADR-0009 §2 (no dev task — it's QA's existing weekly regression, extended). |

## 3. Task breakdown

- **NFR-1: Consolidate mutating-tool denylist into a documented,
  single-source module + CI script** — ADR-0009 §1. Replaces the inline
  bash regex in `.github/workflows/ci.yml`'s existing NFR-1 guard step with
  a call to a tested Python script reading `core/nfr1_denylist.py`.
  Independent of every other task here — ready for dev immediately.
- **NFR-2: Structured log-safety wrapper + CI guard against raw stdlib
  logging** — ADR-0009 §2. New `core/safe_log.py`, allowlist enforcement,
  CI grep guard (new step in the same guard job as task 1, but a separate
  task since the two modules are unrelated in code even though they share a
  CI job). Independent — ready for dev immediately; lands ahead of FR-2/FR-5
  adding their first log call sites (scaffolding precedes features).
- **NFR-2: RLS cross-user isolation harness (ephemeral Postgres + auth.uid()
  stub) — linked_accounts coverage** — ADR-0009 §3. New
  `backend-integration` CI job, `tests/integration/fixtures/auth_stub.sql`,
  `tests/integration/test_rls_isolation.py`, `psycopg[binary]` dev
  dependency. Independent of tasks 1/2 — ready for dev immediately (`linked_accounts`
  already exists, #16 merged).
- **NFR-2: Extend RLS isolation harness to orders/order_items/budgets** —
  adds two more `RLS_TABLES` entries (the `order_items` join-back case and
  the plain `budgets` case) to the harness built above. Blocked by the
  harness task above, by #43 (orders/order_items schema), and by #67
  (budgets schema) — all three must exist before this can run against real
  tables.
- Follow-up docs task (not part of #9's acceptance criteria): add
  `core/nfr1_denylist.py`, `core/safe_log.py`, `scripts/`, and
  `tests/integration/` to `SYSTEM.md` §2's module tree, and record this
  issue's `backend-integration` CI job addition in §5 (Testing strategy).
