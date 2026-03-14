-- Supabase Database Schema for Compliance Platform
-- Run this in Supabase SQL Editor

-- Users table (extends Supabase auth.users)
create table public.users (
  id uuid references auth.users on delete cascade primary key,
  email text unique not null,
  full_name text,
  company_name text,
  dre_license text,
  nmls_id text,
  tier text default 'free' check (tier in ('free', 'pro', 'broker')),
  stripe_customer_id text,
  stripe_subscription_id text,
  subscription_status text default 'inactive' check (subscription_status in ('active', 'inactive', 'cancelled', 'past_due')),
  scans_this_month integer default 0,
  created_at timestamp with time zone default timezone('utc'::text, now()),
  updated_at timestamp with time zone default timezone('utc'::text, now())
);

-- Enable RLS
alter table public.users enable row level security;

-- Users can read/update their own data
create policy "Users can view own profile"
  on public.users for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on public.users for update
  using (auth.uid() = id);

-- Scans table
create table public.scans (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references public.users(id) on delete cascade not null,
  url text not null,
  profession text not null check (profession in ('realestate', 'lending')),
  status text default 'pending' check (status in ('pending', 'running', 'completed', 'failed')),
  score integer check (score >= 0 and score <= 100),
  summary jsonb,
  results jsonb,
  created_at timestamp with time zone default timezone('utc'::text, now()),
  completed_at timestamp with time zone
);

-- Enable RLS
alter table public.scans enable row level security;

create policy "Users can view own scans"
  on public.scans for select
  using (auth.uid() = user_id);

create policy "Users can create own scans"
  on public.scans for insert
  with check (auth.uid() = user_id);

-- Subscriptions table
create table public.subscriptions (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references public.users(id) on delete cascade not null,
  stripe_subscription_id text unique,
  stripe_price_id text,
  tier text not null check (tier in ('pro', 'broker')),
  status text not null check (status in ('active', 'cancelled', 'past_due')),
  current_period_start timestamp with time zone,
  current_period_end timestamp with time zone,
  cancel_at_period_end boolean default false,
  created_at timestamp with time zone default timezone('utc'::text, now()),
  updated_at timestamp with time zone default timezone('utc'::text, now())
);

-- Enable RLS
alter table public.subscriptions enable row level security;

create policy "Users can view own subscriptions"
  on public.subscriptions for select
  using (auth.uid() = user_id);

-- Team members table (for Broker tier)
create table public.team_members (
  id uuid default gen_random_uuid() primary key,
  broker_id uuid references public.users(id) on delete cascade not null,
  member_email text not null,
  member_user_id uuid references public.users(id) on delete set null,
  status text default 'pending' check (status in ('pending', 'active', 'removed')),
  invited_at timestamp with time zone default timezone('utc'::text, now()),
  joined_at timestamp with time zone
);

-- Enable RLS
alter table public.team_members enable row level security;

create policy "Brokers can manage team members"
  on public.team_members for all
  using (auth.uid() = broker_id);

create policy "Members can view own team memberships"
  on public.team_members for select
  using (auth.uid() = member_user_id);

-- Functions

-- Reset scan counts monthly
create or replace function reset_monthly_scans()
returns void as $$
begin
  update public.users set scans_this_month = 0;
end;
$$ language plpgsql;

-- Update updated_at timestamp
create or replace function update_updated_at_column()
returns trigger as $$
begin
  new.updated_at = timezone('utc'::text, now());
  return new;
end;
$$ language plpgsql;

create trigger update_users_updated_at
  before update on public.users
  for each row execute function update_updated_at_column();

create trigger update_subscriptions_updated_at
  before update on public.subscriptions
  for each row execute function update_updated_at_column();

-- Indexes for performance
create index idx_scans_user_id on public.scans(user_id);
create index idx_scans_created_at on public.scans(created_at);
create index idx_subscriptions_user_id on public.subscriptions(user_id);
create index idx_subscriptions_stripe_id on public.subscriptions(stripe_subscription_id);
create index idx_team_members_broker_id on public.team_members(broker_id);
