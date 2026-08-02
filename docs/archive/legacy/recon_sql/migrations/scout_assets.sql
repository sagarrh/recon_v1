-- scout_assets.sql — Tier 2: Scout-side registry of assets (existing GEO content OR Scout target assets)
-- indexed against the recommendation each was built to serve. Idempotent + additive (safe to re-run).
-- Scout never writes to GEO content_tracking/blog_generations; this table is Scout-owned.
CREATE TABLE IF NOT EXISTS scout_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID,                      -- GEO client_id (uuid); joins to content_tracking/blog_generations
    recommendation_id UUID,              -- FK-by-value to recommendations.id (nullable: an existing asset may pre-date any rec)
    run_id UUID,                         -- the Scout run that registered/derived this asset row
    cluster_id TEXT,                     -- target cluster (matches recommendations.cluster_id, TEXT)
    cluster_label TEXT,
    asset_source TEXT NOT NULL DEFAULT 'target'
        CHECK (asset_source IN ('existing', 'target', 'built')),
    asset_type TEXT,                     -- 'blog' | 'faq_page' | 'schema_jsonld' | 'llms_txt' | 'case_study' | 'landing_page' | 'other'
    content_url TEXT NOT NULL DEFAULT '',  -- published URL when known; '' for an unpublished target (plain value so PostgREST ON CONFLICT inference works)
    content_title TEXT,
    published_date DATE,                 -- from content_tracking / blog_generations; NULL for target
    geo_source_table TEXT,               -- 'content_tracking' | 'blog_generations' | NULL for target
    geo_source_id TEXT,                  -- the GEO row id/url that seeded this (audit trail)
    generated_by_vibe_engine BOOLEAN,    -- carried from content_tracking (is this a GEO-builder asset?)
    derived_from JSONB,                  -- {schema_types_missing:[...], action_bullets:[...], target_url, target_url_source}
    asset_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (asset_status IN ('proposed', 'published', 'superseded')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS scout_assets_dedupe_uidx
    ON scout_assets (client_id, recommendation_id, asset_type, content_url);
CREATE INDEX IF NOT EXISTS scout_assets_client_cluster_idx
    ON scout_assets (client_id, cluster_id);
CREATE INDEX IF NOT EXISTS scout_assets_url_idx ON scout_assets (content_url);
