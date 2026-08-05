-- 0200_recon_revenue_categories.sql
-- Manual-apply migration for the Scout write-side tables in Supabase.
--
-- These tables are NOT managed by this repository's migrations/ runner (which owns only
-- ai_visibility_* and aivc_*), so this file must be applied by hand against the Supabase
-- project before the next live Recon run.
--
-- Purpose: replace the SOV-derived revenue estimate with evidence-graded revenue categories.
--   recorded             GA4 purchase revenue on mapped, client-owned target landing pages
--   influenced           recorded revenue linked to target pages/sessions, no sole-causation claim
--   incremental_estimate before/after adjusted by a control or seasonal baseline (later phase)
--   modeled_scenario     assumption-driven planning figure; NOT revenue; internal-only
--   unavailable          insufficient evidence for a defensible value (the honest default)
--
-- ADDITIVE ONLY. The legacy revenue_opportunity_usd / revenue_at_risk_usd / revenue_basis
-- columns are deliberately left in place so this migration is safely reversible. Dropping them
-- belongs in a separate later migration, once the new columns have been populated by a real run.
--
-- Idempotent: safe to re-run. Additive only — no DROP COLUMN, DELETE, UPDATE or TRUNCATE.
-- Wrapped in a transaction, so a failure rolls back whole rather than leaving a partial schema.
--
-- PREFLIGHT (run first; this migration assumes PostgreSQL auto-named the existing inline CHECK):
--
--   select conname, pg_get_constraintdef(oid)
--     from pg_constraint
--    where conrelid = 'public.scout_asset_attribution'::regclass and contype = 'c';
--
-- Expect a constraint named scout_asset_attribution_revenue_source_check. If it is named
-- differently, edit line ~124 to match: DROP CONSTRAINT IF EXISTS silently no-ops on a wrong
-- name, leaving the old four-value constraint in place next to the new one, and every
-- 'insufficient_linkage' write would still be rejected at runtime.

begin;

-- Category values, repeated as a text CHECK per table to match how every other enumerated
-- column in this schema is declared (rec_type, priority, asset_status, ...). A native enum
-- would be tidier but is materially harder to extend, and `incremental_estimate` semantics
-- are still expected to move.
--   'recorded', 'influenced', 'incremental_estimate', 'modeled_scenario', 'unavailable'


-- ---------------------------------------------------------------------------
-- recommendations — the graded revenue evidence for the cluster at ship time.
-- ---------------------------------------------------------------------------
alter table public.recommendations
  add column if not exists revenue_category text not null default 'unavailable',
  add column if not exists revenue_value_usd numeric,
  add column if not exists revenue_currency text not null default 'USD',
  -- Why the value is graded the way it is (assumed capture fraction, unconverted currency, ...).
  -- Travels with the value so a category can never be read without its caveats.
  add column if not exists revenue_limitations jsonb not null default '[]'::jsonb;

alter table public.recommendations
  drop constraint if exists recommendations_revenue_category_check;
alter table public.recommendations
  add constraint recommendations_revenue_category_check check (
    revenue_category in ('recorded', 'influenced', 'incremental_estimate',
                         'modeled_scenario', 'unavailable')
  );

-- A value is meaningless without a category that earns it. `unavailable` must stay NULL
-- rather than becoming 0.
alter table public.recommendations
  drop constraint if exists recommendations_revenue_value_requires_category;
alter table public.recommendations
  add constraint recommendations_revenue_value_requires_category check (
    revenue_value_usd is null or revenue_category <> 'unavailable'
  );


-- ---------------------------------------------------------------------------
-- scout_outcomes — the measured outcome for a shipped recommendation.
-- ---------------------------------------------------------------------------
alter table public.scout_outcomes
  add column if not exists revenue_category text not null default 'unavailable',
  add column if not exists baseline_revenue_category text not null default 'unavailable',
  -- Mapped, client-owned target pages. GA4 revenue is scoped to these and to nothing else;
  -- with no target pages there is no linkage and the outcome is `unavailable`, never
  -- property-wide revenue wearing this recommendation's label.
  add column if not exists target_pages jsonb not null default '[]'::jsonb;


-- ---------------------------------------------------------------------------
-- scout_build_briefs / scout_assets — carried through the Tier-3 build path.
-- ---------------------------------------------------------------------------
alter table public.scout_build_briefs
  add column if not exists revenue_category text not null default 'unavailable',
  add column if not exists revenue_value_usd numeric;
alter table public.scout_build_briefs
  drop constraint if exists scout_build_briefs_revenue_category_check;
alter table public.scout_build_briefs
  add constraint scout_build_briefs_revenue_category_check check (
    revenue_category in ('recorded', 'influenced', 'incremental_estimate',
                         'modeled_scenario', 'unavailable')
  );

alter table public.scout_assets
  add column if not exists revenue_category text not null default 'unavailable',
  add column if not exists revenue_value_usd numeric;
alter table public.scout_assets
  drop constraint if exists scout_assets_revenue_category_check;
alter table public.scout_assets
  add constraint scout_assets_revenue_category_check check (
    revenue_category in ('recorded', 'influenced', 'incremental_estimate',
                         'modeled_scenario', 'unavailable')
  );


-- ---------------------------------------------------------------------------
-- scout_asset_attribution — asset-level linkage.
-- ---------------------------------------------------------------------------
alter table public.scout_asset_attribution
  add column if not exists revenue_category text not null default 'unavailable',
  -- 'attributed' only where an observable link exists; otherwise 'insufficient_linkage'.
  add column if not exists attribution_status text not null default 'insufficient_linkage';

alter table public.scout_asset_attribution
  drop constraint if exists scout_asset_attribution_revenue_category_check;
alter table public.scout_asset_attribution
  add constraint scout_asset_attribution_revenue_category_check check (
    revenue_category in ('recorded', 'influenced', 'incremental_estimate',
                         'modeled_scenario', 'unavailable')
  );

alter table public.scout_asset_attribution
  drop constraint if exists scout_asset_attribution_attribution_status_check;
alter table public.scout_asset_attribution
  add constraint scout_asset_attribution_attribution_status_check check (
    attribution_status in ('attributed', 'insufficient_linkage')
  );

-- REQUIRED: the existing revenue_source CHECK allows only the four legacy channels, so writing
-- an unlinked row would violate it. attribution_events and tier1_modeled stay in the allowed set
-- purely so historical rows remain valid — neither is written any more.
alter table public.scout_asset_attribution
  drop constraint if exists scout_asset_attribution_revenue_source_check;
alter table public.scout_asset_attribution
  add constraint scout_asset_attribution_revenue_source_check check (
    revenue_source in (
      'ga4_landing_page',
      'insufficient_linkage',
      'attribution_events',   -- legacy, no longer written (CRM is out of scope)
      'selection_events',     -- legacy, no longer written (cluster-level, not asset-level)
      'tier1_modeled'         -- legacy, no longer written (allocated share, not attribution)
    )
  );

-- revenue_basis is NOT NULL DEFAULT 'none' and is no longer written by the application.
-- The default keeps existing inserts valid; the column is dropped in a later migration.

-- Only linked rows may carry a dollar. An insufficient_linkage row is NULL, never 0.
--
-- NOT VALID is deliberate and load-bearing. Historical rows carry dollars produced by the old
-- equal-split and modeled-share channels, and the new attribution_status column defaults them to
-- 'insufficient_linkage' — so a validated constraint would fail on every one of them and roll the
-- whole migration back. The two honest alternatives were both worse: back-filling them to
-- 'attributed' would launder an allocated share into evidence of linkage, and nulling their
-- dollars would destroy an audit trail.
--
-- NOT VALID binds every future insert and update while leaving history untouched and readable.
-- Do NOT run VALIDATE CONSTRAINT until the legacy rows have been dealt with deliberately.
alter table public.scout_asset_attribution
  drop constraint if exists scout_asset_attribution_dollars_require_linkage;
alter table public.scout_asset_attribution
  add constraint scout_asset_attribution_dollars_require_linkage check (
    attributed_revenue_usd is null or attribution_status = 'attributed'
  ) not valid;


-- ---------------------------------------------------------------------------
-- Backfill: none, on purpose.
--
-- Legacy rows carried SOV-derived figures this model does not recognise. They grade as
-- `unavailable` via the column defaults, and their old values stay untouched in the legacy
-- columns for audit. No legacy dollar is promoted into revenue_value_usd, and no historical
-- allocation is relabelled as attribution.
-- ---------------------------------------------------------------------------

commit;
