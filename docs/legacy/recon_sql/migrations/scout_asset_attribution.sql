-- scout_asset_attribution.sql — Tier 2: one row per (asset, revenue channel, window) pairing.
-- Correlation, NOT causation (reuses the ATTRIBUTION_BASIS discipline from scout/reports/attribution.py).
-- Every attributed dollar is honesty-tagged; missing GA4/GSC/CRM -> revenue_basis='none', NULL dollars (never 0).
-- 'tier1_modeled' (D2) is the Tier-1 opportunity-share fallback channel — the Phase-A workhorse.
CREATE TABLE IF NOT EXISTS scout_asset_attribution (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scout_asset_id UUID NOT NULL,          -- FK-by-value to scout_assets.id
    recommendation_id UUID,                -- carried for joins/labels
    client_id UUID,
    cluster_id TEXT,
    revenue_source TEXT NOT NULL
        CHECK (revenue_source IN ('ga4_landing_page', 'attribution_events', 'selection_events', 'tier1_modeled')),
    revenue_basis TEXT NOT NULL DEFAULT 'none'
        CHECK (revenue_basis IN ('actual', 'modeled', 'hybrid', 'none')),   -- same enum as Tier 1
    attribution_basis TEXT NOT NULL,       -- human string: 'correlation (page-level ...)' etc. (never 'causation')
    attributed_revenue_usd NUMERIC,        -- NULL when basis='none'; NEVER 0-as-missing
    currency_code TEXT DEFAULT 'USD',      -- normalized to USD by Tier 1's revenue math before storage
    window_start DATE,                     -- = scout_outcomes.week_date (NULL when no measured window yet)
    window_end DATE,                       -- = scout_outcomes.window_elapsed_at
    coverage_score NUMERIC,                -- 0..1: fraction of the cluster dollar this row explains
    confidence_score NUMERIC,              -- 0..1: channel strength x coverage x lag penalty
    revenue_inputs JSONB,                  -- every input + basis_reason + allocation split (provenance)
    measured_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- Windowless rows store the writer's sentinel date ('0001-01-01'), never NULL — a plain
-- unique index dedupes them on every PG version (NULLs would be distinct and duplicate).
CREATE UNIQUE INDEX IF NOT EXISTS scout_asset_attribution_dedupe_uidx
    ON scout_asset_attribution (scout_asset_id, revenue_source, window_start, window_end);
CREATE INDEX IF NOT EXISTS scout_asset_attribution_client_idx
    ON scout_asset_attribution (client_id, cluster_id);
CREATE INDEX IF NOT EXISTS scout_asset_attribution_rec_idx
    ON scout_asset_attribution (recommendation_id);
