-- 0201_recon_recommendation_targets.sql
-- Manual-apply migration for the Scout write-side tables in Supabase.
-- Prerequisite: 0200_recon_revenue_categories.sql
--
-- Purpose: make a recommendation measurable. Until now a recommendation carried only free-text
-- action_bullets, so there was nothing to measure an outcome AGAINST. These columns record the
-- exact client-owned pages and search queries an action targets:
--   target_queries -> the GSC measurement grain
--   target_pages   -> the GA4 measurement grain, and the only route to a `recorded` revenue category
--
-- Pages are validated against the client's own domains in scout/targets.py before they land here.
-- A competitor or third-party URL is dropped and recorded in target_rejections, never stored as a
-- target: attributing a third party's traffic to a client's action is precisely the error the
-- revenue-category work exists to prevent.
--
-- ADDITIVE ONLY. No DROP COLUMN, DELETE, UPDATE or TRUNCATE. Idempotent: safe to re-run.
-- Wrapped in a transaction, so a failure rolls back whole rather than leaving a partial schema.
--
-- PREFLIGHT (confirm 0200 is already applied):
--
--   select column_name from information_schema.columns
--    where table_schema = 'public' and table_name = 'recommendations'
--      and column_name in ('revenue_category', 'revenue_value_usd');
--
-- Expect two rows. If empty, apply 0200 first.

begin;

-- ---------------------------------------------------------------------------
-- recommendations — what this action targets.
-- ---------------------------------------------------------------------------
alter table public.recommendations
  add column if not exists target_pages jsonb not null default '[]'::jsonb,
  add column if not exists target_queries jsonb not null default '[]'::jsonb,
  add column if not exists action_type text not null default 'other',
  add column if not exists mapping_confidence text not null default 'unmapped',
  -- Why a proposed page was refused (unowned domain, unresolvable path, over cap). Kept so an
  -- operator can see why a recommendation is unmapped instead of having to infer it.
  add column if not exists target_rejections jsonb not null default '[]'::jsonb,
  add column if not exists expected_leading_outcome text not null default '',
  add column if not exists expected_business_outcome text not null default '';

alter table public.recommendations
  drop constraint if exists recommendations_action_type_check;
alter table public.recommendations
  add constraint recommendations_action_type_check check (
    action_type in ('content_update', 'new_page', 'schema', 'ai_access',
                    'third_party', 'measurement', 'other')
  );

-- exact      = at least one validated client-owned page (GSC + GA4 measurable)
-- query_only = queries but no page (GSC measurable only)
-- unmapped   = nothing measurable; outcome measurement skips it rather than measure something else
alter table public.recommendations
  drop constraint if exists recommendations_mapping_confidence_check;
alter table public.recommendations
  add constraint recommendations_mapping_confidence_check check (
    mapping_confidence in ('exact', 'query_only', 'unmapped')
  );

-- The grade must match the evidence. NOT VALID: legacy rows predate targeting entirely and default
-- to '[]' / 'unmapped', which already satisfies this — but validating would scan the whole table
-- for no benefit, and any future backfill should be a deliberate step.
alter table public.recommendations
  drop constraint if exists recommendations_mapping_matches_targets;
alter table public.recommendations
  add constraint recommendations_mapping_matches_targets check (
    (mapping_confidence = 'exact'      and jsonb_array_length(target_pages) > 0)
    or (mapping_confidence = 'query_only' and jsonb_array_length(target_pages) = 0
                                          and jsonb_array_length(target_queries) > 0)
    or (mapping_confidence = 'unmapped'   and jsonb_array_length(target_pages) = 0
                                          and jsonb_array_length(target_queries) = 0)
  ) not valid;

commit;
