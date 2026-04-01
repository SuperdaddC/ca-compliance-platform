-- ============================================================
-- 002_user_roles.sql  —  Add role column + auto-create profiles
-- Run this in Supabase SQL Editor (project > SQL Editor > New query)
-- ============================================================

-- 1. Add role column to user_profiles
alter table public.user_profiles
  add column if not exists role text not null default 'user'
  check (role in ('user', 'admin'));

-- 2. Auto-create a user_profiles row when someone signs up
--    This trigger fires after a new row is inserted into auth.users
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.user_profiles (id, email, role)
  values (new.id, new.email, 'user')
  on conflict (id) do nothing;
  return new;
end;
$$ language plpgsql security definer;

-- Drop if exists so this migration is re-runnable
drop trigger if exists on_auth_user_created on auth.users;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- 3. Backfill: create profiles for any existing auth users who don't have one
insert into public.user_profiles (id, email, role)
select id, email, 'user'
from auth.users
where id not in (select id from public.user_profiles)
on conflict (id) do nothing;

-- 4. Set admin role for your accounts
update public.user_profiles
set role = 'admin'
where email in ('mike@thecolyerteam.com', 'mjcolyer@gmail.com');
