-- scout_outcomes.sql — R1-5: per-recommendation outcome lifecycle + ship-time SOV baseline snapshot.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor before the next live run.
-- The GEO mirror is a moving one-way sync (sov_weekly upserts in place), so a pre-recommendation cluster
-- SOV baseline cannot be reconstructed later — it is snapshotted INTO baseline_* at ship time by _write_outcomes.
--
-- R5-1 (M5) APPENDS `ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS ...` below the anchor comment and
-- REUSES m.SCOUT_OUTCOMES_TABLE — it must NEVER re-create the table or re-declare the constant.
CREATE TABLE IF NOT EXISTS scout_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id UUID NOT NULL,
    run_id UUID NOT NULL,
    client_id UUID,
    cluster_id TEXT NOT NULL,
    week_date DATE,
    execution_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (execution_status IN ('proposed', 'accepted', 'executed', 'verified', 'declined')),
    baseline_client_sov_pp FLOAT,
    baseline_competitor_sov_pp FLOAT,
    baseline_primary_competitor TEXT,
    baseline_snapshot_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS scout_outcomes_recommendation_id_uidx
    ON scout_outcomes (recommendation_id);

-- R5-1 appends go below this line (ADD COLUMN IF NOT EXISTS only):
-- R5-1 — deterministic weekly outcome measurement (filled in place when the timeline window elapses;
-- baseline_client_sov_pp is the FROZEN R1-5 ship-time value and is reused, never reconstructed).
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS competitor_name text;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS window_weeks int;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS window_elapsed_at date;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS current_client_sov_pp numeric;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS sov_delta_pp numeric;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS recovered boolean;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS executed boolean;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS field jsonb;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS measured_at timestamptz;
