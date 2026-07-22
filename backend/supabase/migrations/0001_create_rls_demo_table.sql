-- Demonstrates the RLS convention documented in backend/supabase/README.md
-- ("RLS policy template" section) end-to-end against a real table before any
-- feature ships one. Reverted by 0002_drop_rls_demo_table.sql in this same
-- PR — no permanent table results from this pair.

create table if not exists public._rls_convention_demo (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    note text not null
);

alter table public._rls_convention_demo enable row level security;

create policy "select own rows" on public._rls_convention_demo
    for select
    using (auth.uid() = user_id);

create policy "insert own rows" on public._rls_convention_demo
    for insert
    with check (auth.uid() = user_id);

create policy "update own rows" on public._rls_convention_demo
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own rows" on public._rls_convention_demo
    for delete
    using (auth.uid() = user_id);
