-- 0202_recon_priority_score.sql
-- Manual-apply migration for the Scout write-side tables in Supabase.
-- Prerequisite: 0201_recon_recommendation_targets.sql
--
-- Purpose: persist the commercial priority score that replaces the SOV-derived dollar figure as
-- Recon's ordering signal. That figure divided by the client's share of voice, so it ranked
-- clusters by how SMALL the client's presence was rather than how commercially important they
-- were — a 0.1pp client scored ~999x its own revenue.
--
-- priority_components carries the full component vector (value, weight and reason per component,
-- plus which inputs were missing and what share of total weight was available). It is stored so a
-- score can always be explained rather than trusted, and so a weight change is auditable after the
-- fact against the scores it produced.
--
-- ADDITIVE ONLY. No DROP COLUMN, DELETE, UPDATE or TRUNCATE. Idempotent: safe to re-run.
-- Wrapped in a transaction, so a failure rolls back whole rather than leaving a partial schema.
--
-- PREFLIGHT (confirm 0201 is already applied):
--
--   select column_name from information_schema.columns
--    where table_schema = 'public' and table_name = 'recommendations'
--      and column_name in ('target_pages', 'mapping_confidence');
--
-- Expect two rows. If empty, apply 0201 first.

begin;

alter table public.recommendations
  add column if not exists priority_score numeric not null default 0,
  add column if not exists priority_band text not null default 'low',
  add column if not exists priority_components jsonb not null default '{}'::jsonb;

alter table public.recommendations
  drop constraint if exists recommendations_priority_band_check;
alter table public.recommendations
  add constraint recommendations_priority_band_check check (
    priority_band in ('critical', 'high', 'medium', 'low')
  );

alter table public.recommendations
  drop constraint if exists recommendations_priority_score_range;
alter table public.recommendations
  add constraint recommendations_priority_score_range check (
    priority_score >= 0 and priority_score <= 100
  );

-- The operator's working query: highest-priority open recommendations for one client.
create index if not exists recommendations_client_priority_idx
  on public.recommendations(client_id, priority_score desc);

commit;
