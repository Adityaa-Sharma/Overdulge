-- budgets: monthly spend caps, overall and per-category (BRD §5, FR-5).
-- Two partial unique indexes instead of one plain unique(user_id, month,
-- category) — Postgres treats NULL as distinct from itself, so a single
-- constraint would silently allow duplicate overall caps (category is null).
-- RLS per backend/supabase/README.md (ADR-0002): auth.uid() = user_id.

create table public.budgets (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    month date not null,
    category text,
    cap_paise int not null check (cap_paise > 0)
);

create unique index budgets_user_month_category_uq
    on public.budgets (user_id, month, category)
    where category is not null;

create unique index budgets_user_month_overall_uq
    on public.budgets (user_id, month)
    where category is null;

alter table public.budgets enable row level security;

create policy "select own rows" on public.budgets
    for select
    using (auth.uid() = user_id);

create policy "insert own rows" on public.budgets
    for insert
    with check (auth.uid() = user_id);

create policy "update own rows" on public.budgets
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own rows" on public.budgets
    for delete
    using (auth.uid() = user_id);
