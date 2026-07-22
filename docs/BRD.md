# Overdulge — Business Requirements Document

Version 1.0 · 2026-07-23 · Owner: Aditya Sharma · Status: Approved for Phase 1

## 1. Vision

Overdulge is a spend and food intelligence layer on top of quick-commerce and
food-delivery accounts. Users link their Swiggy (Food + Instamart) and Zepto
accounts via delegated OAuth; Overdulge syncs their order history, then gives
them a dashboard, a natural-language query engine, spending projections,
budgeting help, calorie estimation, and smarter reorder recommendations.

Phase 1 platforms: Swiggy Food, Swiggy Instamart, Zepto.
Phase 1 audience: the owner and a closed circle of friends (< 25 users). Not public.

## 2. Verified platform facts (from feasibility probe, 2026-07-23)

These are engineering law. Every sync-related task inherits them as acceptance criteria.

1. All three services expose first-class order-history tools over MCP
   (streamable HTTP, JSON-RPC): Swiggy Food `get_food_orders`,
   Instamart `get_orders`, Zepto `list_order_history`.
2. Auth is OAuth 2.1 + PKCE (S256) with **Dynamic Client Registration** on both
   auth servers (`mcp.swiggy.com/auth`, `auth.zepto.co.in`). The backend
   self-registers its callback; no manual whitelisting is required for deployment.
3. **Instamart history window is ~15 days.** Data is perishable; the daily sync
   is an asset-builder, not a convenience. Insights accumulate from link date.
4. **Swiggy Food history is address-scoped.** A complete history requires
   iterating every `addressId` from `get_addresses` and merging.
5. Money units differ: Zepto = integer paise; Swiggy Food = formatted string
   ("₹273"); Instamart = plain rupee numbers. Canonical storage: integer paise.
6. Timestamps differ: Instamart = ISO-8601 UTC; Swiggy Food = "February 12,
   0:26 AM" with **no year** (resolve via `get_food_order_details` at ingest);
   Zepto list view = no date (resolve via `get_order_detail`).
7. Instamart `orderType` must be `"DASH"` (or omitted). `"INSTAMART"` silently
   returns empty.
8. Never reconstruct totals from components; trust `grandTotal` (Instamart bill
   arithmetic does not close due to unitemized discounts).
9. Zepto `get_past_order_items` returns a pre-aggregated frequency ranking with
   stable `productVariantId` keys — primary substrate for recommendations.
   Instamart `your_go_to_items` is its Swiggy-side counterpart.
10. Mutating tools (cart/order/payment) exist on all services. Phase 1 calls
    **read-only tools exclusively**. Swiggy orders are COD and non-cancellable;
    any accidental order placement is a critical incident.

## 3. Architecture constraints (fixed)

- Frontend: React, static build, deployed to GitHub Pages.
- Backend: **Python** on Cloudflare Workers (Python Workers, `pywrangler`
  workflow), FastAPI for routing.
- LLM: LangChain `init_chat_model`, provider-agnostic. Current provider is
  **Groq**, initialised as `init_chat_model("groq:<model>")` with the model name
  read from config, not hardcoded. Worker secret: `GROQ_API_KEY`.
  Provider is chosen at runtime from a single `LLM_PROVIDER` setting so that
  moving to OpenAI or Azure OpenAI later is a config change only — no
  provider-specific SDK calls anywhere outside the LangChain abstraction.
  Planned migration: OpenAI / Azure OpenAI once keys are available; do not
  design anything that assumes Groq-specific behaviour.
  Note: this is the *product's* LLM only. The agent loop that builds this repo
  runs on Claude via `CLAUDE_CODE_OAUTH_TOKEN` (claude-code-action, billed to
  the Claude subscription) and is entirely unrelated to the product's provider.
- Database: Supabase (Postgres). Backend talks to Supabase over its REST API
  (PostgREST) — do not assume `supabase-py` works under Pyodide.
- Sync: Cloudflare Cron Triggers on the Worker, daily minimum.
- Platform access: backend is an MCP client (JSON-RPC over HTTP via Worker
  fetch). Per-user tokens encrypted at rest in Supabase.
- Secrets: only via Worker secrets / GitHub Actions secrets. Never in code.

## 4. Functional requirements

### FR-1 Authentication & account
- FR-1.1 User signs up / logs in to Overdulge (Supabase Auth; email OTP or
  Google). Session handling on the frontend.
- FR-1.2 User can link Swiggy and Zepto accounts: backend runs OAuth 2.1 + PKCE
  + DCR flow per §2.2; user completes platform OTP on the platform's own page.
- FR-1.3 User can see link status per platform and unlink (token deletion).
- FR-1.4 Tokens stored encrypted; refresh-token rotation handled server-side.

### FR-2 Order sync
- FR-2.1 Daily cron sync per linked account; manual "Sync now" button.
- FR-2.2 Normalization layer producing the canonical order schema (see §5),
  honoring facts §2.4–§2.8.
- FR-2.3 Idempotent upserts keyed on (platform, order_id); cancelled orders
  stored but flagged and excluded from spend totals by default.
- FR-2.4 Sync status surfaced per platform (last sync, orders captured,
  window warnings for Instamart).

### FR-3 Spend dashboard
- FR-3.1 Monthly/weekly spend totals and trends per platform and combined.
- FR-3.2 Category breakdown (food delivery vs grocery; item categories where
  available), top restaurants/products, order frequency, average order value.
- FR-3.3 Spend projection: simple run-rate projection for current month with
  clear "projection" labeling.
- FR-3.4 Location lens: spend by delivery address (free from Food's
  address-scoped history).

### FR-4 Natural-language query engine
- FR-4.1 User asks questions in natural language ("how much did I spend on
  milk since May?"); LangChain agent translates to queries over the canonical
  schema and answers with numbers + a short explanation.
- FR-4.2 Query engine is read-only against the user's own data; row-level
  isolation enforced (Supabase RLS).
- FR-4.3 Graceful "I don't have enough data" behavior; never fabricate totals.

### FR-5 Budgeting
- FR-5.1 User sets monthly caps (overall and per category).
- FR-5.2 Progress display in-app; LLM-generated "where to cut" suggestions
  grounded in the user's actual order lines.
- FR-5.3 Weekly email digest (spend summary + budget status). Provider chosen
  by SA (Resend/SendGrid class); no real-time alerts in Phase 1.

### FR-6 Calorie estimation
- FR-6.1 Order-level calorie estimates for food items via LLM mapping of item
  names to an Indian nutrition reference; grocery items excluded unless
  clearly ready-to-eat.
- FR-6.2 Weekly intake rollups and trend.
- FR-6.3 Every figure visibly labeled "estimate". Tone rules: playful about
  ordering habits and money only — never about the user's body, weight, or
  eating; no prescriptive dieting language.

### FR-7 Recommendations & reorder
- FR-7.1 "Your usuals" from Zepto `get_past_order_items` + Instamart
  `your_go_to_items` + computed Food frequency.
- FR-7.2 Suggested next orders (cheaper and/or lower-calorie alternatives to
  frequent items), grounded in live `search_products` / `search_menu` results.
- FR-7.3 Reorder = redirect links to the item/restaurant on the platform.
  Agentic cart-building is Phase 2; mutating tools remain unused.

## 5. Canonical order schema (minimum)

orders(id, user_id, platform ENUM(swiggy_food, swiggy_instamart, zepto),
platform_order_id UNIQUE with platform, address_id NULL, status,
is_cancelled BOOL, ordered_at TIMESTAMPTZ, grand_total_paise INT,
item_total_paise INT NULL, fees_paise INT NULL, raw JSONB)

order_items(id, order_id FK, name, quantity, unit_price_paise NULL,
platform_item_id NULL, product_variant_id NULL, category NULL, is_veg NULL,
calorie_estimate NULL)

linked_accounts(id, user_id, platform, tokens_encrypted, linked_at,
last_sync_at, sync_state JSONB)

budgets(id, user_id, month, category NULL, cap_paise)

## 6. Non-functional requirements

- NFR-1 Read-only guarantee: no code path may invoke a mutating platform tool.
  CI includes a static check (denylist of mutating tool names) on the backend.
- NFR-2 Privacy: friends-beta still means real personal data. RLS on all user
  tables; tokens encrypted; no order data in logs; probe-style personal dumps
  never committed.
- NFR-3 Free-tier fit: Workers free tier, Supabase free tier, GitHub Pages.
- NFR-4 All LLM outputs that state numbers must be computed from the database,
  not model recall.

## 7. Risks

- R-1 Swiggy policy: repo states third-party app development is not permitted
  pending security review. DCR works today; Builders Club application to be
  submitted for legitimacy. Mitigation: friends-only scale, read-only usage,
  ready to pause Swiggy sync if asked.
- R-2 MCP schema drift: tool names/params may change without notice.
  Mitigation: contract tests against tool schemas run in weekly QA regression.
- R-3 Instamart data loss if sync misses > ~15 days. Mitigation: sync-failure
  issues auto-filed by the sweep.
- R-4 Food year-less timestamps could mis-bucket year-boundary orders.
  Mitigation: detail-call resolution at ingest (§2.6).

## 8. Phase 1 sign-off checklist (BA-owned)

Phase 1 is DONE when every item below maps to a qa-passed feature issue:

- [ ] Sign up / log in
- [ ] Link + unlink Swiggy and Zepto (deployed, DCR flow)
- [ ] Daily sync + manual sync, all three services, normalization per §2
- [ ] Spend dashboard: totals, trends, categories, projection, location lens
- [ ] NL query engine over own data
- [ ] Budget caps + progress + cut suggestions + weekly email digest
- [ ] Calorie estimates + weekly rollups, labeled as estimates
- [ ] Usuals + recommendations + redirect reorder links
- [ ] NFR-1 static check green; RLS verified by a cross-user access test
- [ ] Full QA regression pass on the deployed app
