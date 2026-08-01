-- v_calibration.sql — R5-2 (optional): confidence-tier recovery-rate view over executed outcomes.
-- Idempotent (CREATE OR REPLACE VIEW). Read-only; mirrors scout.db.calibration.compute_calibration for dashboards.
-- Apply in the Supabase SQL editor after scout_outcomes (R1-5/R5-1) exists; safe to re-run.
CREATE OR REPLACE VIEW v_calibration AS
SELECT r.confidence AS tier,
       count(*) FILTER (WHERE o.executed) AS n_executed,
       count(*) FILTER (WHERE o.executed AND o.recovered) AS n_recovered,
       round(avg((o.recovered)::int) FILTER (WHERE o.executed), 4) AS recovery_rate
FROM scout_outcomes o
JOIN recommendations r ON r.id = o.recommendation_id
GROUP BY r.confidence;
