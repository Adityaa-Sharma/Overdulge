-- linked_accounts: encrypted per-user OAuth tokens per platform (BRD §5,
-- ADR-0001). One linked account per (user_id, platform) — the OAuth
-- callback upserts on that pair. RLS per the convention in
-- backend/supabase/README.md (ADR-0002): auth.uid() = user_id.

create type public.linked_platform as enum ('swiggy', 'zepto');

create table public.linked_accounts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    platform public.linked_platform not null,
    tokens_encrypted text not null,
    linked_at timestamptz not null default now(),
    last_sync_at timestamptz,
    sync_state jsonb not null default '{}'::jsonb,
    unique (user_id, platform)
);

alter table public.linked_accounts enable row level security;

create policy "select own rows" on public.linked_accounts
    for select
    using (auth.uid() = user_id);

create policy "insert own rows" on public.linked_accounts
    for insert
    with check (auth.uid() = user_id);

create policy "update own rows" on public.linked_accounts
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own rows" on public.linked_accounts
    for delete
    using (auth.uid() = user_id);
