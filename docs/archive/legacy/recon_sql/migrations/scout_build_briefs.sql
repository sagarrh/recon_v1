-- scout_build_briefs.sql — Tier 3: build-brief intake (one row per revenue-anchored asset to build).
-- Idempotent + additive; FK-by-value (no foreign key constraints), matching scout_outcomes house style.
-- Dedupe: FR-BRIEF-6 — re-running intake never duplicates a brief.
CREATE TABLE IF NOT EXISTS scout_build_briefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id UUID NOT NULL,       -- revenue-opportunity anchor (recommendations.id)
    scout_asset_id UUID,                   -- the Tier-2 target row this brief was seeded from
    client_id UUID,
    cluster_id TEXT,
    cluster_label TEXT,
    asset_class TEXT NOT NULL CHECK (asset_class IN (
        'schema_jsonld','llms_txt','ai_bots_allowlist','robots_txt',
        'faq_page','blog','case_study','landing_page')),
    target TEXT NOT NULL,                  -- resolvable deploy location (URL for pages; site root for llms/robots)
    seed_signals JSONB,                    -- derived_from snapshot + anything else that seeded the brief
    revenue_opportunity_usd NUMERIC,       -- carried from Tier 1 at brief time (never recomputed)
    revenue_basis TEXT CHECK (revenue_basis IN ('actual','modeled','hybrid','none')),
    status TEXT NOT NULL DEFAULT 'drafted'
        CHECK (status IN ('drafted','queued','generating','generated','superseded')),
    edited_by TEXT,
    edited_at TIMESTAMPTZ,
    run_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS scout_build_briefs_dedupe_uidx
    ON scout_build_briefs (recommendation_id, asset_class, target);
CREATE INDEX IF NOT EXISTS scout_build_briefs_client_idx ON scout_build_briefs (client_id, status);
