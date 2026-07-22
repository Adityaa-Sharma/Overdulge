-- Reverts 0001_create_rls_demo_table.sql once the RLS pattern has been
-- verified end-to-end (cross-user isolation confirmed under the
-- `authenticated` role with `auth.uid()` set via JWT claims, per
-- backend/supabase/README.md). This task ships no feature table.

drop table if exists public._rls_convention_demo;
