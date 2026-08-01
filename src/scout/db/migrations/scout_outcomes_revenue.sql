-- scout_outcomes_revenue.sql — Tier 1: frozen ship-time revenue baseline + window-close revenue delta.
-- Idempotent + additive. baseline_revenue_usd / revenue_at_risk_usd are FROZEN at ship time by _write_outcomes;
-- current_revenue_usd / revenue_delta_usd / revenue_basis are filled at window close by outcome_measure.
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS baseline_revenue_usd  numeric;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS revenue_at_risk_usd   numeric;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS current_revenue_usd   numeric;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS revenue_delta_usd     numeric;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS revenue_basis         text DEFAULT 'none';

DO $$ BEGIN
  ALTER TABLE scout_outcomes
    ADD CONSTRAINT scout_outcomes_revenue_basis_chk
    CHECK (revenue_basis IN ('actual','modeled','hybrid','none'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
