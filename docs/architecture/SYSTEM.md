# Overdulge — System Architecture

Owner: SA agent. First written against BRD v1.0 while implementing FR-1
(Authentication & account linking). Extend this file per feature; do not fork
it — one system doc, updated in place, ADRs for point decisions.

## 1. Stack (BRD §3 — fixed, do not deviate without a human-approved BRD change)

- **Frontend**: React (Vite), static build, deployed to GitHub Pages.
- **Backend**: Python on Cloudflare Workers (Python Workers, `pywrangler`),
  FastAPI for routing.
- **LLM**: LangChain `init_chat_model`, provider-agnostic. Current provider is
  Groq (`init_chat_model("groq:<model>")`, `GROQ_API_KEY`), chosen at runtime
  via a single `LLM_PROVIDER` setting; Azure OpenAI is the planned future
  migration (config change only). No provider SDK calls outside the
  LangChain abstraction.
- **Database**: Supabase (Postgres), accessed over its REST API (PostgREST).
  Do not assume `supabase-py` works under Pyodide — use plain HTTP calls
  (`httpx`/`requests`-style) against PostgREST + GoTrue endpoints.
- **Sync**: Cloudflare Cron Triggers on the Worker, daily minimum.
- **Platform access**: backend is an MCP client (JSON-RPC over streamable
  HTTP via Worker `fetch`). Per-user platform tokens encrypted at rest in
  Supabase.
- **Secrets**: Worker secrets / GitHub Actions secrets only, never in code.

## 2. Module boundaries

```
backend/
  worker.py         Cloudflare Worker fetch entrypoint (must stay at this
                    top level — Pyodide's import root is the directory of
                    wrangler.toml's `main`, so it has to sit beside `app/`
                    for `from app...` imports to resolve)
  app/
    main.py         FastAPI app + router wiring
    api/             route modules, one per feature area:
                      auth.py, links.py, sync.py, dashboard.py, query.py,
                      budgets.py, calories.py, recommendations.py
    core/
      config.py      typed settings loader, reads Worker secrets/env only
      auth.py        Supabase JWT verification (JWKS), FastAPI dependency
                      that resolves the authenticated user_id
      db.py           PostgREST HTTP client wrapper (anon-key + user-JWT
                      forwarding mode, and service-role mode — see ADR-0002)
      crypto.py       symmetric encrypt/decrypt for tokens at rest
    oauth/
      engine.py       generic OAuth 2.1 + PKCE(S256) + DCR flow — see
                      ADR-0001
      platforms/       swiggy.py, zepto.py — per-platform metadata/config only
    mcp/
      client.py       MCP JSON-RPC client (streamable HTTP)
      adapters/        swiggy_food.py, swiggy_instamart.py, zepto.py —
                      per-platform tool call wrappers + normalization
    sync/
      cron.py         Cron Trigger entrypoint, iterates linked_accounts
      normalize.py    canonical schema mapping (BRD §2.4-§2.8, §5)
    llm/
      agent.py        LangChain agent(s) over the canonical schema
  tests/
    unit/
    integration/
    contracts/         stored MCP tool schemas, diffed weekly by QA
                      (agents/qa.md Mode B) — never call live platforms
                      from CI
  pyproject.toml, wrangler.toml

frontend/
  src/
    routes/            auth, dashboard, settings, budgets, ... (one route
                      group per feature area)
    lib/
      supabase.ts       Supabase Auth client, session persistence
      api.ts            backend API client, attaches Supabase JWT to every
                      call, redirects to /login on 401
    components/
  tests/
```

Rule: platform-specific knowledge (tool names, payload shapes, quirks from
BRD §2.4-§2.9) lives only inside `mcp/adapters/*` and `oauth/platforms/*`.
Everything else (routes, sync loop, LLM, frontend) is platform-agnostic and
talks to the canonical schema or the generic OAuth engine.

## 3. Data flow

1. **Auth**: frontend authenticates directly against Supabase Auth (GoTrue) —
   email OTP or Google — and holds the session/JWT client-side
   (`lib/supabase.ts`). No backend involvement in login itself.
2. **Authenticated API calls**: frontend attaches `Authorization: Bearer
   <supabase_jwt>` to every backend call. `core/auth.py` verifies the JWT
   against Supabase's JWKS and resolves `user_id`; unauthenticated requests
   are rejected (401) before reaching any route handler (BRD AC-8).
3. **Account linking**: `POST /api/v1/links/{platform}/start` (authenticated)
   → backend generates a PKCE pair + opaque `state`, persists them
   server-side keyed to `(user_id, platform)`, returns the platform's
   authorization URL. Frontend redirects the browser there; user completes
   OTP on the platform's own page; platform redirects to the backend
   callback; backend exchanges the code, encrypts the resulting tokens, and
   upserts `linked_accounts`. Full mechanics in ADR-0001.
4. **Sync**: Cron Trigger → `sync/cron.py` iterates `linked_accounts` →
   decrypts tokens in-memory only → `mcp/client.py` calls the platform's
   read-only history tool(s) → `sync/normalize.py` maps to the canonical
   schema (BRD §5) → idempotent upsert via PostgREST keyed on
   `(platform, platform_order_id)`.
5. **Reads** (dashboard, NL query, budgets, calories, recommendations):
   backend reads the canonical schema scoped to the authenticated user. See
   ADR-0002 for how RLS is enforced on these reads vs. the service-role
   writes used by linking/sync.
6. **NL query** (FR-4): `POST /api/v1/query` (user-JWT mode only, never
   service-role) → `llm/agent.py` runs a capped LangChain tool-calling loop
   over the fixed read-only tool set in `llm/tools.py` → the returned
   number is the tool's raw structured output (formatted in code, never
   re-typed by the model) with a model-generated explanation alongside it.
   See ADR-0003 for why this is tool-calling over a fixed set rather than
   text-to-SQL, and how "not enough data" is distinguished from a true `₹0`.
7. **Recommendations** (FR-7): `api/recommendations.py` builds "usuals" from
   Zepto `get_past_order_items` + Instamart `your_go_to_items` (live calls,
   pre-aggregated by the platform) plus a Food frequency ranking computed
   over already-synced `order_items`. Suggested alternatives are fetched via
   live `search_products`/`search_menu` at request time — never cached or
   persisted — and compared against the frequent item on price and (where
   both sides have one) calorie estimate. Reorder links are `redirect_url`
   fields produced by the adapters themselves, never constructed in the
   route. See ADR-0004.

## 4. Conventions

- **Money**: integer paise everywhere past ingest (BRD §2.5). Never
  reconstruct totals from line items — trust `grand_total_paise` (BRD §2.8).
- **Timestamps**: canonical storage is UTC `TIMESTAMPTZ`. Platform quirks
  (Swiggy Food's year-less string, Zepto list view's missing date) are
  resolved via the documented detail-call fallback at ingest time (BRD
  §2.6), never stored ambiguous.
- **API shape**: REST under `/api/v1/...`, JSON bodies, errors as
  `{"error": {"code": "...", "message": "..."}}`.
- **Config**: all secrets via `core/config.py`, sourced from Worker
  secrets/env only — never hard-coded, never logged.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`,
  `chore:`).
- **NFR-1**: no mutating platform tool name may appear in `backend/`
  outside `tests/contracts/` (enforced by the CI denylist grep in
  `.github/workflows/ci.yml`) — read-only tools only, every platform,
  always.
- **NFR-2**: no order data or token material in logs; no real personal data
  in committed fixtures.
- **NFR-4**: any LLM-generated answer that states a number must source that
  number from a DB-backed tool/query result, never from the model's own
  text — see ADR-0003's grounding mechanism for the pattern (code renders
  the number, the model only explains it).

## 5. Testing strategy

- **Backend**: `pytest` (`uv run pytest`), `ruff` for lint. Unit tests per
  module; integration tests run against recorded fixtures in
  `tests/contracts/` and `tests/integration/fixtures/` — never against live
  platform accounts. The NFR-1 static guard in CI is a backstop, not a
  substitute for not writing mutating calls.
- **Frontend**: `vitest` + React Testing Library for components/routes;
  `npm run build` succeeding is itself a deploy gate (`deploy.yml`).
- **Contracts**: `tests/contracts/` stores the MCP tool schemas each
  adapter depends on; QA's weekly regression (agents/qa.md Mode B) fetches
  live `tools/list` and diffs against these — drift files a P1 bug, not a
  silent break.
- Every acceptance criterion in a task issue must have a corresponding test;
  the PR template's requirement→implementation→test table is the checklist.

## 6. Decisions

See `docs/architecture/decisions/`:
- [ADR-0001](decisions/0001-oauth-pkce-dcr-and-token-encryption.md) — OAuth
  2.1 + PKCE(S256) + DCR flow design, token encryption at rest.
- [ADR-0002](decisions/0002-supabase-access-pattern.md) — backend Supabase
  access pattern (service-role vs. user-JWT forwarding) and RLS convention.
- [ADR-0003](decisions/0003-nl-query-engine-tool-calling.md) — NL query
  engine: fixed tool-calling set over PostgREST (not text-to-SQL), numeric
  grounding enforcement, "not enough data" vs. true-zero disambiguation.
- [ADR-0004](decisions/0004-usuals-and-live-recommendation-matching.md) —
  "usuals" ranking source per platform, live (never cached) alternative
  matching, shared calorie-estimation function reuse, adapter-owned
  redirect URLs.
