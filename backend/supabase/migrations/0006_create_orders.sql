-- orders / order_items: canonical normalized order schema (BRD §5, ADR-0005).
--
-- `order_platform` has THREE values, unlike `linked_accounts.platform`'s two
-- (`swiggy`/`zepto`) — a single Swiggy OAuth link fans out into
-- `swiggy_food` and `swiggy_instamart` sync sub-flows (ADR-0005 §1).
--
-- `unique (platform, platform_order_id)` is the idempotent-upsert key
-- (BRD AC-8): `sync/normalize.py::upsert_orders` upserts on this pair.
--
-- `vendor_name` is additive to BRD §5's minimum schema, folded into this
-- migration per the SA's comment on #43 (see
-- docs/architecture/features/4-spend-dashboard.md §4): FR-3's "top
-- restaurants" (AC-4) needs restaurant identity at the order level, since
-- `order_items.name` is a dish, not a restaurant. Nullable; only
-- `swiggy_food` populates it (task #49).
--
-- `order_items` has no `user_id` column, so its RLS policies join back to
-- `orders` per backend/supabase/README.md.

create type public.order_platform as enum ('swiggy_food', 'swiggy_instamart', 'zepto');

create table public.orders (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    platform public.order_platform not null,
    vendor_name text,
    platform_order_id text not null,
    address_id text,
    status text not null,
    is_cancelled boolean not null default false,
    ordered_at timestamptz not null,
    grand_total_paise int not null,
    item_total_paise int,
    fees_paise int,
    raw jsonb not null,
    constraint orders_platform_platform_order_id_uq unique (platform, platform_order_id)
);

alter table public.orders enable row level security;

create policy "select own rows" on public.orders
    for select
    using (auth.uid() = user_id);

create policy "insert own rows" on public.orders
    for insert
    with check (auth.uid() = user_id);

create policy "update own rows" on public.orders
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own rows" on public.orders
    for delete
    using (auth.uid() = user_id);

create table public.order_items (
    id uuid primary key default gen_random_uuid(),
    order_id uuid not null references public.orders (id) on delete cascade,
    name text not null,
    quantity int not null,
    unit_price_paise int,
    platform_item_id text,
    product_variant_id text,
    category text,
    is_veg boolean,
    calorie_estimate int
);

alter table public.order_items enable row level security;

create policy "select own rows" on public.order_items
    for select
    using (exists (
        select 1 from public.orders
        where orders.id = order_items.order_id and orders.user_id = auth.uid()
    ));

create policy "insert own rows" on public.order_items
    for insert
    with check (exists (
        select 1 from public.orders
        where orders.id = order_items.order_id and orders.user_id = auth.uid()
    ));

create policy "update own rows" on public.order_items
    for update
    using (exists (
        select 1 from public.orders
        where orders.id = order_items.order_id and orders.user_id = auth.uid()
    ))
    with check (exists (
        select 1 from public.orders
        where orders.id = order_items.order_id and orders.user_id = auth.uid()
    ));

create policy "delete own rows" on public.order_items
    for delete
    using (exists (
        select 1 from public.orders
        where orders.id = order_items.order_id and orders.user_id = auth.uid()
    ));
