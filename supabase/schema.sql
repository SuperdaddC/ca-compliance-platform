-- ============================================================
-- ComplyWithJudy  —  Supabase Schema v2
-- Run this in Supabase SQL Editor (project > SQL Editor > New query)
-- ============================================================

-- ----------------------------------------------------------------
-- 1. Users (extends Supabase auth.users)
-- ----------------------------------------------------------------
create table if not exists public.user_profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  email         text not null,
  full_name     text,
  created_at    timestamptz default now()
);

alter table public.user_profiles enable row level security;

create policy "Users read own profile"
  on public.user_profiles for select
  using (auth.uid() = id);

create policy "Users update own profile"
  on public.user_profiles for update
  using (auth.uid() = id);

-- ----------------------------------------------------------------
-- 2. Subscriptions  (annual model)
-- ----------------------------------------------------------------
create table if not exists public.user_subscriptions (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid references auth.users(id) on delete cascade,
  plan                text not null check (plan in ('starter','professional','broker','single')),
  status              text not null default 'active' check (status in ('active','canceled','past_due','trialing')),
  stripe_customer_id  text,
  stripe_sub_id       text,       -- null for single-scan (one-time)
  stripe_price_id     text,
  scans_remaining     integer,    -- null = unlimited; set for starter(5) professional(25)
  domains_allowed     integer default 1,
  current_period_end  timestamptz,
  cancel_at_period_end boolean default false,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now()
);

alter table public.user_subscriptions enable row level security;

create policy "Users read own subscription"
  on public.user_subscriptions for select
  using (auth.uid() = user_id);

-- Service role can write (called from backend only)
create policy "Service role full access subscriptions"
  on public.user_subscriptions for all
  using (auth.role() = 'service_role');

-- ----------------------------------------------------------------
-- 3. Scans
-- ----------------------------------------------------------------
create table if not exists public.scans (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid references auth.users(id) on delete set null,
  email           text,           -- captured even for anon scans
  url             text not null,
  profession      text not null check (profession in ('realestate','lending')),
  status          text not null default 'pending'
                  check (status in ('pending','running','completed','failed')),
  score           integer check (score between 0 and 100),
  result          jsonb,          -- full check results
  error_type      text,           -- timeout | blocked | dns_fail | ssl_error | empty_page | internal
  error_message   text,           -- human-readable error for dashboard display
  screenshot_path text,           -- Supabase storage path
  is_free_scan    boolean default true,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

alter table public.scans enable row level security;

-- Users see their own scans
create policy "Users read own scans"
  on public.scans for select
  using (auth.uid() = user_id);

-- Anon scans readable by email match (used on results page)
create policy "Anon scans readable by scan id"
  on public.scans for select
  using (user_id is null);

create policy "Service role full access scans"
  on public.scans for all
  using (auth.role() = 'service_role');

-- Index for fast dashboard queries
create index if not exists scans_user_id_idx on public.scans(user_id, created_at desc);
create index if not exists scans_status_idx  on public.scans(status);

-- ----------------------------------------------------------------
-- 4. IP + Email Fingerprints  (free scan abuse prevention)
-- ----------------------------------------------------------------
create table if not exists public.scan_fingerprints (
  id          uuid primary key default gen_random_uuid(),
  fingerprint text not null unique,   -- sha256(ip:email)
  email       text not null,
  scan_id     uuid references public.scans(id) on delete set null,
  used_at     timestamptz default now()
);

-- No RLS needed — only service role touches this table
alter table public.scan_fingerprints enable row level security;

create policy "Service role full access fingerprints"
  on public.scan_fingerprints for all
  using (auth.role() = 'service_role');

create index if not exists fingerprints_fp_idx on public.scan_fingerprints(fingerprint);

-- ----------------------------------------------------------------
-- 5. Stripe webhook events  (idempotency log)
-- ----------------------------------------------------------------
create table if not exists public.stripe_events (
  id            text primary key,   -- Stripe event ID (evt_...)
  type          text not null,
  payload       jsonb,
  processed_at  timestamptz default now()
);

alter table public.stripe_events enable row level security;

create policy "Service role full access stripe_events"
  on public.stripe_events for all
  using (auth.role() = 'service_role');

-- ----------------------------------------------------------------
-- 6. Email capture  (remarketing)
-- ----------------------------------------------------------------
create table if not exists public.email_leads (
  id          uuid primary key default gen_random_uuid(),
  email       text not null unique,
  source      text default 'scan',  -- scan | pricing | footer
  converted   boolean default false,
  created_at  timestamptz default now()
);

alter table public.email_leads enable row level security;

create policy "Service role full access leads"
  on public.email_leads for all
  using (auth.role() = 'service_role');

-- ----------------------------------------------------------------
-- 7. Helpful views
-- ----------------------------------------------------------------

-- Dashboard scan list (what the frontend queries)
create or replace view public.scan_summary as
  select
    s.id,
    s.url,
    s.profession,
    s.status,
    s.score,
    s.error_type,
    s.error_message,
    s.is_free_scan,
    s.created_at,
    sub.plan
  from public.scans s
  left join public.user_subscriptions sub
    on sub.user_id = s.user_id and sub.status = 'active'
  where s.user_id = auth.uid()
  order by s.created_at desc;

-- ----------------------------------------------------------------
-- 8. Pricing reference  (source of truth for Stripe sync)
-- ----------------------------------------------------------------
-- Stripe Price IDs — fill these in after creating products in Stripe dashboard
-- Starter:       $29/year  — 5 scans
-- Professional:  $79/year  — 25 scans
-- Broker:        $199/year — unlimited, 10 domains
-- Single:        $19 once  — 1 scan

comment on table public.user_subscriptions is
  'Starter=$29/yr/5scans | Professional=$79/yr/25scans | Broker=$199/yr/unlimited | Single=$19/once';
