-- recommendations_window_weeks.sql — P1B: persist the structured measurement-window upper bound.
-- Idempotent + additive (safe to re-run). Apply in the Supabase SQL editor before the next live run.
-- recommendation_gen now computes window_weeks alongside the timeline prose; outcome_measure prefers
-- this column and falls back to prose-string matching only for legacy rows where it is NULL.
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS window_weeks int;
