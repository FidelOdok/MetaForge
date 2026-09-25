-- MetaForge Cloud control plane.
--
-- This schema lives in Supabase and holds ONLY the broker's own state:
-- who has an account, which projects they have, and where their gateway is.
-- It deliberately holds no design data. The digital twin, the work products
-- and the tool adapters stay on the user's own gateway; the cloud brokers a
-- connection to them. That is what keeps "there is no MetaForge cloud holding
-- your IP" literally true.
--
-- Row-level security is the tenant boundary. Not application filtering — RLS.
-- A missed `WHERE account_id = ...` in a query is a data leak; a missed row in
-- a policy-protected table returns nothing. Every table below has RLS enabled
-- and FORCEd, so even the table owner is subject to it.
--
-- Order matters here: tables, then the membership helpers, then the policies
-- that use them. `language sql` function bodies are parsed at CREATE time, so
-- a helper defined before its tables fails outright.

-- ===========================================================================
-- 1. Tables
-- ===========================================================================

-- accounts — the ownership and billing root -------------------------------

create table if not exists public.accounts (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (length(trim(name)) between 1 and 200),
  slug        text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'),
  -- 'personal' is created automatically on sign-up so a new user has somewhere
  -- to put a project before thinking about teams.
  kind        text not null default 'personal' check (kind in ('personal', 'org')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

comment on table public.accounts is
  'MetaForge Cloud account — the ownership root for projects and gateways.';

-- account_members — user <-> account ---------------------------------------

create table if not exists public.account_members (
  account_id  uuid not null references public.accounts(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  role        text not null default 'member' check (role in ('owner', 'admin', 'member')),
  created_at  timestamptz not null default now(),
  primary key (account_id, user_id)
);

comment on table public.account_members is
  'Membership and role of a user within an account.';

create index if not exists account_members_user_idx on public.account_members (user_id);

-- cloud_projects — an account-scoped project -------------------------------
--
-- MetaForge already represents project membership in three uncoordinated
-- places: the `projects` / `project_work_products` tables in the gateway's own
-- Postgres (which feed the Projects page), a `project_id` attribute on every
-- twin node (which feeds the /twin filter), and `metadata.project_id` on
-- knowledge chunks. This is a FOURTH, and it lives in a different database
-- entirely, so it cannot be kept in sync implicitly.
--
-- `gateway_project_id` is therefore an explicit, nullable pointer at the
-- gateway-side id, not an assumption that the two agree. Nullable because a
-- cloud project can exist before it has been bound to anything on a gateway.

create table if not exists public.cloud_projects (
  id                  uuid primary key default gen_random_uuid(),
  account_id          uuid not null references public.accounts(id) on delete cascade,
  name                text not null check (length(trim(name)) between 1 and 200),
  -- Text, not uuid: the gateway stores these as VARCHAR(36) and we must not
  -- silently reject a value it considers valid.
  gateway_project_id  text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (account_id, name)
);

comment on table public.cloud_projects is
  'Account-scoped project. Metadata only — design data stays on the user''s gateway.';
comment on column public.cloud_projects.gateway_project_id is
  'Explicit pointer to the gateway-side project id. Never assume it matches this row''s id.';

create index if not exists cloud_projects_account_idx on public.cloud_projects (account_id);

-- gateway_connections — where an account's gateway lives --------------------
--
-- On the credential: the MCP spec forbids passing the client's token through
-- to an upstream service ("MUST NOT pass through the token it received from
-- the MCP client"). So the Phase 2 proxy authenticates the MCP client with its
-- Supabase token and then reaches the user's gateway with a SEPARATE
-- credential — this one.
--
-- That secret is not stored here. `credential_secret_id` references Supabase
-- Vault, so the ciphertext never sits in a table any client policy can select.
-- `credential_hint` is the last few characters, so the UI can show which
-- credential is configured without revealing it.

create table if not exists public.gateway_connections (
  id                    uuid primary key default gen_random_uuid(),
  account_id            uuid not null references public.accounts(id) on delete cascade,
  label                 text not null default 'Default'
                          check (length(trim(label)) between 1 and 100),
  -- Enforced https: the proxy will send a bearer credential to this address.
  base_url              text not null check (base_url ~ '^https://'),
  credential_secret_id  uuid,
  credential_hint       text check (credential_hint is null or length(credential_hint) <= 8),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (account_id, label)
);

comment on table public.gateway_connections is
  'Address of an account''s self-hosted gateway. The proxy credential lives in Vault, not here.';

create index if not exists gateway_connections_account_idx
  on public.gateway_connections (account_id);

-- ===========================================================================
-- 2. Membership helpers
--
-- These exist to break RLS recursion. A policy on `account_members` that
-- itself queries `account_members` re-enters the policy and Postgres raises
-- "infinite recursion detected". SECURITY DEFINER bypasses RLS inside the
-- function body, which cuts the loop.
--
-- `search_path = ''` is mandatory on SECURITY DEFINER functions: without it a
-- caller can prepend a schema they control and have the function resolve to
-- their own table. Every reference below is therefore schema-qualified.
-- ===========================================================================

create or replace function public.is_account_member(target_account uuid)
returns boolean
language sql
security definer
stable
set search_path = ''
as $$
  select exists (
    select 1
    from public.account_members m
    where m.account_id = target_account
      and m.user_id = (select auth.uid())
  );
$$;

comment on function public.is_account_member(uuid) is
  'True when the current user belongs to the account. SECURITY DEFINER to avoid RLS recursion.';

create or replace function public.is_account_admin(target_account uuid)
returns boolean
language sql
security definer
stable
set search_path = ''
as $$
  select exists (
    select 1
    from public.account_members m
    where m.account_id = target_account
      and m.user_id = (select auth.uid())
      and m.role in ('owner', 'admin')
  );
$$;

comment on function public.is_account_admin(uuid) is
  'True when the current user can administer the account (owner or admin).';

-- ===========================================================================
-- 3. Row-level security
-- ===========================================================================

alter table public.accounts enable row level security;
alter table public.accounts force row level security;

drop policy if exists accounts_select_own on public.accounts;
create policy accounts_select_own on public.accounts
  for select to authenticated
  using (public.is_account_member(id));

drop policy if exists accounts_update_admin on public.accounts;
create policy accounts_update_admin on public.accounts
  for update to authenticated
  using (public.is_account_admin(id))
  with check (public.is_account_admin(id));

-- No INSERT or DELETE policy. Accounts are created by the sign-up trigger
-- below, which runs as SECURITY DEFINER. Letting clients create accounts
-- freely would make the slug namespace a free-for-all.

alter table public.account_members enable row level security;
alter table public.account_members force row level security;

drop policy if exists account_members_select on public.account_members;
create policy account_members_select on public.account_members
  for select to authenticated
  using (public.is_account_member(account_id));

drop policy if exists account_members_write_admin on public.account_members;
create policy account_members_write_admin on public.account_members
  for all to authenticated
  using (public.is_account_admin(account_id))
  with check (public.is_account_admin(account_id));

alter table public.cloud_projects enable row level security;
alter table public.cloud_projects force row level security;

drop policy if exists cloud_projects_read on public.cloud_projects;
create policy cloud_projects_read on public.cloud_projects
  for select to authenticated
  using (public.is_account_member(account_id));

drop policy if exists cloud_projects_write on public.cloud_projects;
create policy cloud_projects_write on public.cloud_projects
  for all to authenticated
  using (public.is_account_member(account_id))
  with check (public.is_account_member(account_id));

alter table public.gateway_connections enable row level security;
alter table public.gateway_connections force row level security;

-- Writes are admin-only, unlike projects: pointing an account's gateway
-- somewhere else redirects every tool call the account makes.
drop policy if exists gateway_connections_read on public.gateway_connections;
create policy gateway_connections_read on public.gateway_connections
  for select to authenticated
  using (public.is_account_member(account_id));

drop policy if exists gateway_connections_write on public.gateway_connections;
create policy gateway_connections_write on public.gateway_connections
  for all to authenticated
  using (public.is_account_admin(account_id))
  with check (public.is_account_admin(account_id));

-- ===========================================================================
-- 4. updated_at maintenance
-- ===========================================================================

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists accounts_touch on public.accounts;
create trigger accounts_touch before update on public.accounts
  for each row execute function public.touch_updated_at();

drop trigger if exists cloud_projects_touch on public.cloud_projects;
create trigger cloud_projects_touch before update on public.cloud_projects
  for each row execute function public.touch_updated_at();

drop trigger if exists gateway_connections_touch on public.gateway_connections;
create trigger gateway_connections_touch before update on public.gateway_connections
  for each row execute function public.touch_updated_at();

-- ===========================================================================
-- 5. Sign-up: give every new user a personal account
--
-- Without this a freshly signed-up user belongs to nothing, every RLS policy
-- denies them, and the dashboard shows an empty shell with no way out of it.
-- ===========================================================================

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  base_slug text;
  final_slug text;
  suffix int := 0;
  new_account_id uuid;
begin
  -- Derive a slug from the email local part; fall back to the user id.
  base_slug := regexp_replace(
    lower(split_part(coalesce(new.email, ''), '@', 1)), '[^a-z0-9]+', '-', 'g'
  );
  base_slug := trim(both '-' from base_slug);
  if length(base_slug) < 3 then
    base_slug := 'user-' || substr(new.id::text, 1, 8);
  end if;

  -- Slugs are unique account-wide and two people can share an email local part
  -- across providers. Probe for a free one rather than failing the sign-up.
  final_slug := base_slug;
  while exists (select 1 from public.accounts a where a.slug = final_slug) loop
    suffix := suffix + 1;
    final_slug := base_slug || '-' || suffix::text;
  end loop;

  insert into public.accounts (name, slug, kind)
  values (coalesce(new.email, 'Personal'), final_slug, 'personal')
  returning id into new_account_id;

  insert into public.account_members (account_id, user_id, role)
  values (new_account_id, new.id, 'owner');

  return new;
end;
$$;

comment on function public.handle_new_user() is
  'Creates a personal account and owner membership for each new auth user.';

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
