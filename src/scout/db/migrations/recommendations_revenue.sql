-- recommendations_revenue.sql — Tier 1: per-recommendation revenue quantification + honesty tag.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor before the next live run.
-- recommendation_gen computes these via scout/revenue.py; NULL/'none' on legacy rows and flags-off runs.
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS revenue_opportunity_usd numeric;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS revenue_at_risk_usd     numeric;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS revenue_basis           text DEFAULT 'none';
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS revenue_inputs          jsonb;

DO $$ BEGIN
  ALTER TABLE recommendations
    ADD CONSTRAINT recommendations_revenue_basis_chk
    CHECK (revenue_basis IN ('actual','modeled','hybrid','none'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
