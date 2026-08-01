-- decision_log_floor_stamp.sql — R0-7: record the active severity-floor set + version on every scout_decision_log row.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor before the next live run; until applied,
-- _write_decision_log inserts are caught by its per-helper try/except and logged, so old code keeps working.
ALTER TABLE scout_decision_log ADD COLUMN IF NOT EXISTS floor_set jsonb;
ALTER TABLE scout_decision_log ADD COLUMN IF NOT EXISTS severity_floor_version text;
