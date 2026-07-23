-- A human-readable label for the delivery address (e.g. "Home", "Work", the
-- user's own tag for it), captured at sync time from Swiggy's get_addresses.
-- The "spend by location" lens showed the opaque address_id before this,
-- because the id is the only address field the order payload itself carries.
-- Nullable: older rows, and platforms without an address concept, have none.
alter table public.orders add column if not exists address_label text;
