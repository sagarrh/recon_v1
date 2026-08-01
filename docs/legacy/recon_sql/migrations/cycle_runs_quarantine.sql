-- cycle_runs_quarantine.sql — R1-4: per-run quarantine telemetry on the cycle_runs lifecycle row.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor before the next live run; until applied,
-- finish_run's new _FINISH_RUN_OPTIONAL_FIELDS entries target missing columns and the whole cycle_runs UPDATE
-- errors into finish_run's try/except — so apply this first or lose that run's cycle telemetry.
ALTER TABLE cycle_runs ADD COLUMN IF NOT EXISTS quarantine_count int DEFAULT 0;
ALTER TABLE cycle_runs ADD COLUMN IF NOT EXISTS quarantine_by_reason jsonb DEFAULT '{}'::jsonb;
