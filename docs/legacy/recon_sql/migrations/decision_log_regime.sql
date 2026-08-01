-- decision_log_regime.sql — R4-3: record which detection regime (news_mode|graduated) produced each verdict.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor before the next live run; until applied,
-- _write_decision_log inserts are caught by its per-helper try/except and logged, so old code keeps working.
ALTER TABLE scout_decision_log ADD COLUMN IF NOT EXISTS graduation_regime text;
