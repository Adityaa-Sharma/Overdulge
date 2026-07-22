-- oauth_pending_links: short-lived server-side PKCE state for the link flow
-- (ADR-0001). One active row per (user_id, platform) — a new
-- POST /links/{platform}/start overwrites any prior pending attempt.
-- Looked up by `state` on the callback (unauthenticated endpoint, so that
-- lookup runs in service-role mode per ADR-0002 — RLS is still enabled here
-- per the convention, it just isn't the enforcement path for that call).
-- TTL is enforced at the application layer (callers filter
-- `expires_at > now()`); no DB-level expiry cleanup ships in this task.

create table public.oauth_pending_links (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    platform public.linked_platform not null,
    code_verifier text not null,
    state text not null,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    unique (user_id, platform)
);

create unique index oauth_pending_links_state_key on public.oauth_pending_links (state);

alter table public.oauth_pending_links enable row level security;

create policy "select own rows" on public.oauth_pending_links
    for select
    using (auth.uid() = user_id);

create policy "insert own rows" on public.oauth_pending_links
    for insert
    with check (auth.uid() = user_id);

create policy "update own rows" on public.oauth_pending_links
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own rows" on public.oauth_pending_links
    for delete
    using (auth.uid() = user_id);
