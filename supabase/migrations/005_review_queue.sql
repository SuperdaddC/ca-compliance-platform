-- =============================================================================
-- Migration 005: Review Queue + Review Assets
-- Internal admin manual review workflow for ambiguous scanner results
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table: review_queue
-- One row per rule per scan that needs human verification
-- -----------------------------------------------------------------------------
create table if not exists public.review_queue (
  id               uuid primary key default gen_random_uuid(),
  scan_id          uuid references public.scans(id) on delete set null,

  -- Site identification
  site_url         text not null,      -- original URL submitted for scanning
  page_url         text,               -- final URL after redirects (what was actually scanned)
  profession       text not null,
  entity_type      text default 'standard',
  score            integer,

  -- Rule identification (matches scanner RuleResult output)
  rule_id          text not null,      -- e.g., 'equal_housing', 'responsible_broker'
  rule_name        text not null,      -- e.g., 'Equal Housing Opportunity'
  scanner_status   text not null,      -- pass / fail / warn / skip
  scanner_detail   text,               -- scanner description + detail combined
  scanner_evidence text,               -- extracted evidence snippet

  -- Version tracking
  scanner_version  text,               -- deployed scanner version (e.g., 'v1.2')
  rule_version     text,               -- rule engine version

  -- Review workflow
  review_status    text not null default 'pending'
                   check (review_status in ('pending', 'claimed', 'completed', 'skipped')),
  claimed_by       uuid references auth.users(id),
  claimed_at       timestamptz,

  -- Decision
  decision         text
                   check (decision in (
                     'confirmed_violation',
                     'false_positive',
                     'not_applicable',
                     'needs_rescan',
                     'scanner_bug'
                   )),
  reviewer_id      uuid references auth.users(id),
  reviewer_note    text,

  -- Regression tagging
  bug_tag          text,               -- free text: eho_svg, ccpa_subpage, js_rendering, etc.

  -- Population source
  source           text not null default 'auto'
                   check (source in ('auto', 'manual', 'backfill')),

  -- Timestamps
  created_at       timestamptz default now(),
  reviewed_at      timestamptz,
  updated_at       timestamptz default now()
);

-- Prevent duplicate pending/claimed items for same site+rule
create unique index if not exists review_queue_pending_uniq
  on public.review_queue(site_url, rule_id)
  where review_status in ('pending', 'claimed');

-- Query performance indexes
create index if not exists review_queue_status_idx on public.review_queue(review_status);
create index if not exists review_queue_rule_idx on public.review_queue(rule_id);
create index if not exists review_queue_created_idx on public.review_queue(created_at desc);
create index if not exists review_queue_claimed_idx on public.review_queue(claimed_by)
  where review_status = 'claimed';

-- -----------------------------------------------------------------------------
-- Table: review_assets
-- Screenshots and attachments linked to review queue items
-- -----------------------------------------------------------------------------
create table if not exists public.review_assets (
  id               uuid primary key default gen_random_uuid(),
  review_item_id   uuid not null references public.review_queue(id) on delete cascade,
  asset_type       text not null default 'screenshot'
                   check (asset_type in ('screenshot', 'annotation', 'document')),
  storage_path     text not null,      -- Supabase Storage path: review-assets/{filename}
  filename         text,               -- original filename if uploaded manually
  mime_type        text,               -- e.g., 'image/jpeg', 'image/png'
  uploaded_by      uuid references auth.users(id),
  caption          text,               -- optional description
  created_at       timestamptz default now()
);

create index if not exists review_assets_item_idx on public.review_assets(review_item_id);

-- -----------------------------------------------------------------------------
-- View: review_queue_stats
-- Aggregated counts for the admin dashboard header
-- -----------------------------------------------------------------------------
create or replace view public.review_queue_stats as
  select
    review_status,
    count(*) as total,
    count(*) filter (where decision = 'confirmed_violation') as confirmed,
    count(*) filter (where decision = 'false_positive') as false_positives,
    count(*) filter (where decision = 'scanner_bug') as bugs_found,
    count(*) filter (where decision = 'not_applicable') as not_applicable,
    count(*) filter (where decision = 'needs_rescan') as needs_rescan
  from public.review_queue
  group by review_status;

-- -----------------------------------------------------------------------------
-- RLS Policies
-- -----------------------------------------------------------------------------
alter table public.review_queue enable row level security;
alter table public.review_assets enable row level security;

-- Service role: full access (used by backend scanner)
create policy "Service role full access on review_queue"
  on public.review_queue for all
  using (auth.role() = 'service_role');

create policy "Service role full access on review_assets"
  on public.review_assets for all
  using (auth.role() = 'service_role');

-- Admins: full access (used by admin UI via JWT)
create policy "Admins full access on review_queue"
  on public.review_queue for all
  using (
    exists (
      select 1 from public.user_profiles
      where id = auth.uid() and role = 'admin'
    )
  );

create policy "Admins full access on review_assets"
  on public.review_assets for all
  using (
    exists (
      select 1 from public.user_profiles
      where id = auth.uid() and role = 'admin'
    )
  );

-- -----------------------------------------------------------------------------
-- Updated_at trigger (reuse existing function if available)
-- -----------------------------------------------------------------------------
create or replace function public.update_review_queue_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger review_queue_updated_at
  before update on public.review_queue
  for each row execute function public.update_review_queue_updated_at();
