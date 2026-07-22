-- oauth_clients: one Dynamic-Client-Registration result per platform per
-- deployment, not per user (ADR-0001). Deployment-global config, not user
-- data — RLS enabled with zero policies: service-role only, per the
-- convention in backend/supabase/README.md.

create table public.oauth_clients (
    platform public.linked_platform primary key,
    client_id text not null,
    client_secret_encrypted text not null,
    expires_at timestamptz,
    registered_at timestamptz not null default now()
);

alter table public.oauth_clients enable row level security;
-- intentionally no policies: service-role only
