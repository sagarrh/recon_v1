-- scout_assets_builder.sql — Tier 3: builder lifecycle + artifact columns on the Tier-2 scout_assets table.
-- ALTER-only, idempotent + additive (ADD COLUMN IF NOT EXISTS); Tier 2 owns the table definition.
-- lifecycle_status has NO DEFAULT deliberately: NULL until the builder registers a generation,
-- so Tier-2 target/existing rows are never falsely stamped 'generated' (D9).
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS build_brief_id UUID;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS target TEXT;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS drafted_payload TEXT;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS content_provenance JSONB;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS revenue_opportunity_usd NUMERIC;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS revenue_basis TEXT
    CHECK (revenue_basis IN ('actual','modeled','hybrid','none'));
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS lifecycle_status TEXT
    CHECK (lifecycle_status IN (
        'generated','internal_approved','client_approved','handed_off',
        'verified','verify_failed','needs_edit','rejected','superseded'));
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS content_tracking_url TEXT;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS handed_off_at TIMESTAMPTZ;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS verify_evidence JSONB;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS verify_reason TEXT;
ALTER TABLE scout_assets ADD COLUMN IF NOT EXISTS asset_revision INT DEFAULT 1;
CREATE INDEX IF NOT EXISTS scout_assets_lifecycle_idx ON scout_assets (client_id, lifecycle_status);
