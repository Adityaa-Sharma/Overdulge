-- budgets: monthly spend caps, overall and per-category (BRD §5, FR-5).
--
-- `category` is NOT NULL: the overall cap is stored under the reserved
-- sentinel '__overall__' rather than true NULL. This is required, not
-- cosmetic — PostgREST's `on_conflict` query param only ever emits a bare
-- `ON CONFLICT (col, ...)` target, which Postgres will only match against a
-- non-partial unique constraint (a bare column-list target cannot attach
-- the `WHERE` predicate a partial index needs to be inferred as the
-- arbiter; see PostgREST issue postgrest/postgrest#1118). A NULL-based
-- design needing two partial unique indexes therefore cannot be upserted
-- through PostgREST at all (42P10). The sentinel keeps a single ordinary
-- unique index, which uniformly satisfies `on_conflict=user_id,month,category`
-- for both the per-category and overall-cap branches, while still
-- preventing duplicate overall caps (a real NULL would compare distinct
-- from itself under a plain unique constraint). `core/budgets.py` translates
-- '__overall__' <-> None at the boundary so every other layer keeps seeing
-- `category: None` for the overall cap.
-- RLS per backend/supabase/README.md (ADR-0002): auth.uid() = user_id.

create table public.budgets (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    month date not null,
    category text not null,
    cap_paise int not null check (cap_paise > 0),
    constraint budgets_user_month_category_uq unique (user_id, month, category)
);

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
