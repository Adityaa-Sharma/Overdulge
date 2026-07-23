-- Stubs Supabase's `auth` schema on a bare postgres:16-alpine instance so
-- migrations that reference auth.uid() (backend/supabase/README.md's RLS
-- policy template) apply unmodified. auth.uid() mirrors Supabase's real
-- implementation: it reads the request.jwt.claim.sub GUC that PostgREST
-- sets per-request via set_config(). `authenticated` is a non-superuser,
-- non-owner role — a table-owning superuser bypasses RLS entirely, so
-- tests must run as this role (via SET ROLE) or the whole suite is a false
-- pass. It is NOLOGIN because tests SET ROLE into it from an already
-- authenticated superuser connection, the same way PostgREST's
-- "authenticator" role switches per-request instead of holding a separate
-- login for every Postgres role it can assume.
create schema if not exists auth;

create or replace function auth.uid() returns uuid
    language sql
    stable
    as $$
        select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
    $$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin noinherit;
    end if;
end
$$;

grant usage on schema public to authenticated;
grant usage on schema auth to authenticated;
grant execute on function auth.uid() to authenticated;
