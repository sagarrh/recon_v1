-- scout_asset_approvals.sql — Tier 3: APPEND-ONLY human-approval ledger (FR-APPROVE-4).
-- Every decision is a NEW row; nothing is ever updated or deduped. FK-by-value (no foreign key constraints).
CREATE TABLE IF NOT EXISTS scout_asset_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scout_asset_id UUID NOT NULL,          -- FK-by-value to scout_assets.id
    gate TEXT NOT NULL CHECK (gate IN ('internal','external')),
    decision TEXT NOT NULL CHECK (decision IN (
        'internal_approved','needs_edit','client_approved','changes_requested','rejected')),
    reviewer TEXT NOT NULL,                -- delivery-owner / client-approver identity (mandatory)
    notes TEXT,
    asset_revision INT,                    -- which generation revision this decision judged
    decided_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS scout_asset_approvals_asset_idx ON scout_asset_approvals (scout_asset_id);
