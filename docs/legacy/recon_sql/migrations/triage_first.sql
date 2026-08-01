-- Triage-First Scout — schema migrations (branch `tiage`).
-- Apply manually in the Supabase SQL editor, in order. Statements are idempotent
-- (safe to re-run). NOTE: the TFS-07 block runs a ONE-TIME cleanup that DELETES legacy
-- duplicate recommendation/report rows from pre-field-resolution runs (it snapshots both
-- tables into *_backup_tiage first).
-- Live table names per scout/db/sed_mapping.py: recommendations, reports, sov_tracking.

-- TFS-05 — pre-publish validation gate (mark-and-write) -----------------------
-- A failed gate sets validation_status='quarantined' + notes; the row still
-- persists (audit) but slack_delivery skips it.
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS validation_status text  DEFAULT 'ok';
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS validation_notes  jsonb DEFAULT '[]'::jsonb;
ALTER TABLE reports         ADD COLUMN IF NOT EXISTS validation_status text  DEFAULT 'ok';
ALTER TABLE reports         ADD COLUMN IF NOT EXISTS validation_notes  jsonb DEFAULT '[]'::jsonb;

-- TFS-07 — one verdict per client-cluster -------------------------------------
-- recommendations now upsert on (run_id, client_id, cluster_id) (was per competitor).
-- Pre-field-resolution runs have multiple rows per (run, client, cluster) — one per
-- competitor — which block the unique index. Snapshot, dedupe (keep one per group),
-- then build the index. Only non-NULL client_id rows can violate it (NULLs are distinct).

-- 0) safety snapshots (drop once verified)
CREATE TABLE IF NOT EXISTS recommendations_backup_tiage AS TABLE recommendations;
CREATE TABLE IF NOT EXISTS reports_backup_tiage          AS TABLE reports;

-- 1) delete reports linked to losing duplicate recommendations
WITH ranked AS (
    SELECT id, row_number() OVER (
             PARTITION BY run_id, client_id, cluster_id ORDER BY id DESC
           ) AS rn
    FROM recommendations
    WHERE client_id IS NOT NULL
)
DELETE FROM reports r USING ranked
WHERE r.recommendation_id = ranked.id AND ranked.rn > 1;

-- 2) delete the legacy duplicate recommendations
WITH ranked AS (
    SELECT id, row_number() OVER (
             PARTITION BY run_id, client_id, cluster_id ORDER BY id DESC
           ) AS rn
    FROM recommendations
    WHERE client_id IS NOT NULL
)
DELETE FROM recommendations r USING ranked
WHERE r.id = ranked.id AND ranked.rn > 1;

-- 3) build the unique index the upsert targets
CREATE UNIQUE INDEX IF NOT EXISTS recommendations_run_client_cluster_uidx
    ON recommendations (run_id, client_id, cluster_id);
-- DROP INDEX IF EXISTS recommendations_run_competitor_cluster_uidx;  -- after verifying the new path

-- TFS-11 — per-run triage decision-log (observability) ------------------------
-- One row per client-cluster verdict: inputs (deltas), resulting severity/priority, noise flag.
CREATE TABLE IF NOT EXISTS scout_decision_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    client_id UUID,
    cluster_id TEXT NOT NULL,
    cluster_label TEXT,
    week_date DATE,
    primary_competitor TEXT,
    primary_delta_pp FLOAT,
    delta_client_pp FLOAT,
    triage_severity TEXT,
    investigation_priority TEXT,
    noise BOOLEAN DEFAULT FALSE,
    field JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS scout_decision_log_run_client_cluster_uidx
    ON scout_decision_log (run_id, client_id, cluster_id);

-- R2-4 — numeric-provenance shadow metric (per-run, per-node/model hallucinated-number rate) -----
-- Written by finish_run from validation_gate's numeric_provenance_metric. Idempotent + additive.
ALTER TABLE cycle_runs ADD COLUMN IF NOT EXISTS hallucinated_number_metric jsonb DEFAULT '{}'::jsonb;
