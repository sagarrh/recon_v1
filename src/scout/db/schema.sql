-- Scout Agent DB Schema
-- Source of truth for all Scout tables.
-- Applied to Supabase for Phase 2 live mode.

-- Onboarding / clients
CREATE TABLE IF NOT EXISTS onboarding (
    client_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Query clusters per client
CREATE TABLE IF NOT EXISTS cluster_visibility_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES onboarding(client_id),
    cluster_id UUID NOT NULL,
    cluster_label TEXT NOT NULL,
    week_date DATE NOT NULL,
    client_sov_score FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Weekly competitor SOV tracking
CREATE TABLE IF NOT EXISTS competitor_sov_tracking (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES onboarding(client_id),
    cluster_id UUID NOT NULL,
    competitor_name TEXT NOT NULL,
    competitor_domain TEXT,
    week_date DATE NOT NULL,
    sov_score FLOAT,
    rolling_avg_4w FLOAT,
    rolling_std_4w FLOAT,
    change_vs_avg FLOAT,
    z_score FLOAT,
    client_sov_this_week FLOAT,
    client_sov_change_vs_avg FLOAT,
    alert_triggered BOOLEAN DEFAULT FALSE,
    alert_type TEXT CHECK (alert_type IN ('gain', 'loss', 'displacement', 'new_entrant')),
    alert_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Investigation jobs
CREATE TABLE IF NOT EXISTS competitor_investigations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES onboarding(client_id),
    cluster_id UUID NOT NULL,
    competitor_name TEXT NOT NULL,
    competitor_domain TEXT,
    week_date DATE NOT NULL,
    shift_type TEXT CHECK (shift_type IN ('gain', 'loss', 'displacement', 'new_entrant')),
    shift_magnitude FLOAT,
    investigation_priority TEXT CHECK (investigation_priority IN ('urgent', 'standard', 'opportunistic')),
    triage_reason TEXT,
    website_changes JSONB,
    third_party_signals JSONB,
    ai_citation_changes JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Recommendations
CREATE TABLE IF NOT EXISTS scout_recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id UUID REFERENCES competitor_investigations(id),
    client_id UUID REFERENCES onboarding(client_id),
    competitor_name TEXT NOT NULL,
    cluster_id UUID NOT NULL,
    cluster_label TEXT,
    shift_type TEXT,
    type TEXT CHECK (type IN ('offensive', 'defensive')),
    priority TEXT CHECK (priority IN ('urgent', 'standard', 'opportunistic')),
    probable_cause TEXT,
    confidence TEXT CHECK (confidence IN ('high', 'medium', 'low', 'unknown')),
    gap_analysis TEXT,
    action_bullets JSONB,
    summary TEXT,
    slack_report TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Competitor website snapshots
CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor_domain TEXT NOT NULL,
    cluster_id UUID,
    snapshot_date DATE NOT NULL,
    pages JSONB,
    total_pages_crawled INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- Triage-First initiative (branch `tiage`) — LIVE-table migrations (reference).
-- The authoritative, ordered script is scout/db/migrations/triage_first.sql.
-- NOTE: live table names differ from the legacy DDL above (recommendations vs
-- scout_recommendations) — reference only; do not run this block as-is.
--   TFS-05: ALTER recommendations/reports ADD validation_status, validation_notes
--   TFS-07: recommendations/reports unique key -> (run_id, client_id, cluster_id)
--   TFS-11: CREATE TABLE scout_decision_log
-- ============================================================================
