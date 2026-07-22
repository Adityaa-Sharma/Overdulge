# Supabase schema & migrations

Migrations are plain numbered SQL files under `migrations/`, applied in
order, oldest first. No migration framework/CLI dependency — `psql` against
the project's direct Postgres connection string is the lowest-friction
option that needs no paid Supabase tier and no extra tooling install.

## Convention

- File name: `NNNN_description.sql` (four-digit, zero-padded, monotonically
  increasing — `0001_...`, `0002_...`, ...). Never renumber or edit an
  already-applied file; to undo one, add a new migration that reverts it.
- Every migration that creates a user-scoped table enables RLS and attaches
  policies in the *same* file that creates the table — never a table without
  RLS, even transiently (ADR-0002).
- One statement group per concern; prefer several small migrations over one
  large one.

## Apply command

Get the direct connection string from the Supabase dashboard: Project
Settings -> Database -> Connection string -> URI (use the "Session pooler"
or direct-connection string, not the pgbouncer transaction-mode one, since
migrations run DDL). Export it as `SUPABASE_DB_URL` and never commit it:

```bash
export SUPABASE_DB_URL="postgresql://postgres.<project-ref>:<password>@<host>:5432/postgres"

for f in backend/supabase/migrations/*.sql; do
  echo "applying $f"
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

`ON_ERROR_STOP=1` is required — without it `psql` keeps going past a failed
statement and can leave a migration half-applied.

To apply a single new migration once existing ones are already live, run
just that one file through the same `psql "$SUPABASE_DB_URL" -v
ON_ERROR_STOP=1 -f backend/supabase/migrations/000N_....sql` command.

## RLS policy template

Every user-scoped table (has a `user_id uuid` column referencing the
authenticated user) gets RLS enabled plus the four CRUD policies in the same
migration that creates it:

```sql
alter table public.<table_name> enable row level security;

create policy "select own rows" on public.<table_name>
    for select
    using (auth.uid() = user_id);

create policy "insert own rows" on public.<table_name>
    for insert
    with check (auth.uid() = user_id);

create policy "update own rows" on public.<table_name>
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "delete own rows" on public.<table_name>
    for delete
    using (auth.uid() = user_id);
```

Tables with no `user_id` column (deployment-global config, e.g.
`oauth_clients`) still get RLS enabled, with zero policies attached —
everyone is denied by default and only the service-role key (which bypasses
RLS entirely) can read or write:

```sql
alter table public.<deployment_global_table> enable row level security;
-- intentionally no policies: service-role only
```

### Demonstrated end-to-end

`migrations/0001_create_rls_demo_table.sql` created a throwaway
`_rls_convention_demo` table using this exact template, and
`migrations/0002_drop_rls_demo_table.sql` reverts it — both ship in the same
PR that added this README. Verified locally against a disposable
`postgres:16-alpine` container (with a stub `auth.uid()` reading the
`request.jwt.claim.sub` GUC, matching Supabase's real implementation, and a
non-superuser `authenticated` role so RLS is actually enforced rather than
bypassed by table ownership):

- User A, authenticated with their own `user_id`, saw only their own rows.
- User B, authenticated with a different `user_id`, saw only their own rows.
- A third user with no rows saw zero rows.
- User A attempting to insert a row with `user_id` set to User B's id was
  rejected with `new row violates row-level security policy`.

No table remains after `0002` runs — the pair proves the pattern without
leaving a permanent unused table in the schema.

## PostgREST access modes (ADR-0002)

`backend/app/core/db.py` exposes two client factories, mirroring the two
trust levels the backend needs against the same Supabase project — see
[ADR-0002](../../docs/architecture/decisions/0002-supabase-access-pattern.md)
for the full rationale:

- `service_role_client()` — sends `apikey` and `Authorization: Bearer` both
  set to `SUPABASE_SERVICE_ROLE_KEY`. Bypasses RLS entirely. Import only
  from `sync/` and `oauth/`; every call site must scope its own queries with
  an explicit `user_id` filter since the database will not do it.
- `user_client(jwt)` — sends `apikey: <anon key>` and `Authorization: Bearer
  <the caller's Supabase JWT>`, forwarded from the incoming request. RLS
  policies are the enforcement layer here; every `api/*` route handler uses
  this mode.

Both return a `PostgrestClient` with generic `select`/`insert`/`upsert`/
`delete` helpers over PostgREST's REST conventions (`GET`/`POST`/`DELETE`
against `/rest/v1/<table>`, `Prefer` headers for representation and
upsert-conflict resolution). No table-specific code lives in `db.py` —
callers pass table names, columns, and filters.
