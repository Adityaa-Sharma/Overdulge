# ADR-0001: OAuth 2.1 + PKCE(S256) + DCR flow design, and token encryption at rest

Status: Accepted
Context issue: #2 (FR-1 Authentication & account linking)

## Context

BRD §2.2 (verified platform facts, engineering law) and FR-1.2/FR-1.4 require:
- OAuth 2.1 + PKCE (S256) with Dynamic Client Registration against both
  `mcp.swiggy.com/auth` and `auth.zepto.co.in`; the backend self-registers
  its own callback — no manual whitelisting.
- Tokens stored encrypted at rest; refresh-token rotation handled
  server-side; no plaintext token ever logged or returned to the frontend.

Both platforms are structurally identical from the backend's point of view
(RFC 8414 authorization-server metadata + RFC 7591-style DCR + PKCE S256),
differing only in base URLs and scopes. The flow should therefore be built
once and configured per platform.

## Decision

### Shared engine, per-platform config
`backend/app/oauth/engine.py` implements the full flow; `oauth/platforms/{swiggy,zepto}.py`
supply only: auth-server metadata discovery URL, scopes, and the MCP base
URL the linked account will later be used against. Adding Zepto after Swiggy
is a config addition, not a new flow.

### Dynamic Client Registration is per-deployment, not per-user
The Worker registers one OAuth client per platform per deployment (not one
per linking user). The resulting `client_id`/`client_secret` (and
`client_secret_expires_at` if the platform issues one) are cached — not
re-registered on every link attempt. Storage: a small `oauth_clients` table
(`platform` PK, `client_id`, `client_secret_encrypted`, `expires_at`,
`registered_at`), written once by whichever request first needs it (register
if missing or expired, else reuse). Encrypted with the same mechanism as
user tokens (below).

### PKCE state lives server-side, never in the browser
The `code_verifier` must never reach the frontend or browser history. Flow:

1. `POST /api/v1/links/{platform}/start` (authenticated, requires a valid
   Supabase session) — backend generates `code_verifier` (random, ≥43
   chars) and `code_challenge = BASE64URL(SHA256(code_verifier))`, plus an
   opaque `state`. Persists `{user_id, platform, code_verifier, state,
   created_at, expires_at}` in a short-lived `oauth_pending_links` table
   (TTL ~10 minutes, one active row per `(user_id, platform)` — new call
   overwrites any prior pending attempt). Returns the platform's
   `authorization_endpoint` URL with `code_challenge`,
   `code_challenge_method=S256`, `state`, and `redirect_uri` = the backend's
   own callback (`/api/v1/links/{platform}/callback`).
2. Frontend redirects the browser to that URL. User completes the
   platform's own OTP/login there — Overdulge never sees platform
   credentials.
3. Platform redirects the browser to the backend callback with `code` +
   `state`.
4. Backend looks up `oauth_pending_links` by `state`; rejects on
   missing/expired/mismatched `user_id`. Exchanges `code` + `code_verifier`
   at the platform's `token_endpoint`. Deletes the pending row (one-shot).
5. Backend encrypts the returned `access_token`/`refresh_token`/expiry,
   upserts `linked_accounts` (`user_id`, `platform`, `tokens_encrypted`,
   `linked_at = now()`, `last_sync_at = null`, `sync_state = {}`).
6. Backend redirects the browser back to the frontend settings route with a
   `?linked=swiggy&status=ok` (or `status=error`) query param — never a
   token, never a code, in that redirect.

### Token encryption at rest
Symmetric AEAD (AES-256-GCM) via a Worker secret `TOKEN_ENCRYPTION_KEY`
(32 bytes, generated once, stored as a Worker secret — never in the repo).
`core/crypto.py` exposes `encrypt(plaintext) -> ciphertext_b64` /
`decrypt(ciphertext_b64) -> plaintext`; both `linked_accounts.tokens_encrypted`
and `oauth_clients.client_secret_encrypted` use it. Decrypted values exist
only in-memory for the duration of a token exchange, refresh, or sync call —
never logged (BRD NFR-2), never included in any API response body (link
status endpoints return only `{platform, linked: bool, linked_at}`).

### Refresh-token rotation
On sync or on-demand platform calls: if the platform returns 401 or the
stored token is within a short expiry buffer, use the stored
`refresh_token` against `token_endpoint` (`grant_type=refresh_token`),
re-encrypt, and overwrite the `linked_accounts` row in the same request
(read-modify-write, no partial state — if the refresh call fails, the old
tokens are left untouched and the sync marks that account's `sync_state` as
needing re-link rather than silently dropping the account).

## Consequences

- One flow implementation to test and audit for both platforms; Zepto's
  task (blocked on Swiggy's) is materially smaller.
- Requires the new `oauth_pending_links` and `oauth_clients` tables beyond
  BRD §5's minimum schema — both are link-flow plumbing, not user-facing
  data, and are in scope for the FR-1 linked_accounts task.
- The callback is a public, unauthenticated endpoint by necessity (the
  platform redirects the raw browser there); its only trust anchor is the
  `state` value matching a pending row, so `state` must be unguessable
  (backend-generated, ≥128 bits) and single-use.
