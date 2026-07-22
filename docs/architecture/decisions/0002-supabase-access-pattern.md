# ADR-0002: Backend Supabase access pattern (service-role vs. user-JWT forwarding) and RLS convention

Status: Accepted
Context issue: #2 (FR-1 Authentication & account linking)

## Context

BRD §3 fixes Supabase (Postgres via PostgREST) as the datastore and NFR-2
requires row-level isolation. FR-4.2 explicitly requires RLS-enforced
isolation for the NL query engine. The backend needs two different trust
levels against the same database:

- **System-initiated writes** with no browser-present user context: the
  daily sync cron writing `orders`/`order_items`, and the OAuth callback
  (step 4/5 of ADR-0001) writing `linked_accounts` — the browser is mid
  third-party-redirect at that point, not attached to an authenticated
  Overdulge session in a way that's convenient to forward.
- **User-initiated reads/writes** proxied through the backend on behalf of
  a logged-in user (dashboard queries, NL query engine, budgets, link
  status, unlink).

## Decision

Two PostgREST access modes, chosen per call site:

1. **Service-role mode** (`SUPABASE_SERVICE_ROLE_KEY`, bypasses RLS): used
   only by `sync/cron.py` and the OAuth callback handler in
   `oauth/engine.py`. Both already have the correct `user_id` from
   trusted server-side state (the `linked_accounts`/`oauth_pending_links`
   row, not user input), so RLS isn't the safety mechanism there — the
   query itself is scoped explicitly with `user_id = :id` in every
   statement. Service-role calls are confined to `sync/` and `oauth/`; no
   other module may import the service-role client.
2. **User-JWT-forwarding mode** (anon key + `Authorization: Bearer
   <supabase_jwt>` forwarded from the incoming request): used by every
   `api/*` route that reads or writes on behalf of the logged-in user
   (dashboard, query engine, budgets, link status, unlink). Postgres RLS
   policies (`auth.uid() = user_id`) are the enforcement layer — a bug in
   route code cannot leak cross-user rows because the database itself
   won't return them. `core/db.py` exposes both clients; `core/auth.py`'s
   dependency makes the verified JWT available to route handlers so it can
   be forwarded, not just decoded for `user_id`.

RLS policy convention, applied to every user-scoped table (`linked_accounts`,
`orders`, `order_items`, `budgets`, and the two link-flow tables from
ADR-0001): `USING (auth.uid() = user_id)` for select/update/delete,
`WITH CHECK (auth.uid() = user_id)` for insert. Tables with no `user_id`
column (`oauth_clients`, which is deployment-global, not user data) have RLS
enabled with no policies — service-role only, everyone else denied by
default.

## Consequences

- Unlink and link-status reads go through the user-JWT path even though
  they're "just" a `linked_accounts` row — consistent with "RLS is the
  isolation mechanism for anything a browser-driven request can trigger",
  and it means a future route added by mistake without a `user_id` filter
  still can't leak.
- Service-role usage is grep-able and confined to two directories, which
  keeps the NFR-2 cross-user isolation test (BRD §8 sign-off item) narrow:
  audit `sync/` and `oauth/` for correct explicit scoping; everything else
  is provably covered by RLS.
- The scaffolding task for Supabase schema/migrations must ship RLS enabled
  and policies attached in the same migration that creates each table —
  never a table without RLS, even transiently.
