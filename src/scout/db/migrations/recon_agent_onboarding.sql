-- recon_agent_onboarding.sql — Recon agent: Scout-owned client "facts of record" (Tier 3).
-- Populated by scout/builders/facts_recon.py (scrape client site -> LLM extract -> upsert); read by
-- scout/db/client_context.get_client_profile to un-starve the asset builder (products/sameAs/serviceType/
-- description/differentiators). Idempotent + additive; FK-by-value (no foreign key constraints), matching
-- the scout_* house style. Exactly one profile per client (1:1 with onboarding.client_id).
CREATE TABLE IF NOT EXISTS recon_agent_onboarding (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,                     -- FK-by-value to onboarding.client_id (1:1)
    company_name TEXT,
    domain TEXT,
    description TEXT,                             -- authoritative 1-3 sentence company description
    products JSONB DEFAULT '[]'::jsonb,           -- [{"name","description"}] named offerings
    services JSONB DEFAULT '[]'::jsonb,           -- service taxonomy terms (serviceType candidates)
    service_type TEXT,
    area_served JSONB DEFAULT '[]'::jsonb,         -- served regions (schema.org areaServed)
    differentiators JSONB DEFAULT '[]'::jsonb,     -- specific factual capability / proof claims
    same_as JSONB DEFAULT '[]'::jsonb,             -- identity URLs (LinkedIn/Crunchbase/G2/Wikidata)
    logo_url TEXT,
    rating_value NUMERIC,                          -- grounded from a review site (G2/Capterra) via Bright Data; never fabricated
    review_count INTEGER,
    rating_source TEXT,                            -- the review-site URL the rating came from
    gaps JSONB DEFAULT '[]'::jsonb,                 -- client-side GEO gap recommendations derived at recon time
    competitive_narrative JSONB DEFAULT '{}'::jsonb, -- grounded LLM parity narrative over collected competitor signals
    contact_point JSONB,                          -- {"telephone","email","contactType","url"}
    provenance JSONB DEFAULT '{}'::jsonb,          -- {field -> source URL} — keeps every extracted fact honest
    source TEXT NOT NULL DEFAULT 'recon_agent'     -- how the profile was populated
        CHECK (source IN ('recon_agent','manual','blog_generations')),
    confidence TEXT DEFAULT 'low'                  -- extraction confidence (how much groundable substance)
        CHECK (confidence IN ('high','medium','low')),
    refreshed_at TIMESTAMPTZ,                      -- last recon run (freshness gate reads this)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Self-healing: CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so columns added after the table's
-- first creation need an explicit idempotent ALTER (safe to re-run any number of times).
ALTER TABLE recon_agent_onboarding ADD COLUMN IF NOT EXISTS rating_value NUMERIC;
ALTER TABLE recon_agent_onboarding ADD COLUMN IF NOT EXISTS review_count INTEGER;
ALTER TABLE recon_agent_onboarding ADD COLUMN IF NOT EXISTS rating_source TEXT;
ALTER TABLE recon_agent_onboarding ADD COLUMN IF NOT EXISTS gaps JSONB DEFAULT '[]'::jsonb;
ALTER TABLE recon_agent_onboarding ADD COLUMN IF NOT EXISTS competitive_narrative JSONB DEFAULT '{}'::jsonb;

-- One profile per client. Plain single-column unique index (no expression, NOT NULL column) so PostgREST
-- can infer it for upsert on_conflict=client_id — a plain index avoids the 42P10 an expression index causes.
CREATE UNIQUE INDEX IF NOT EXISTS recon_agent_onboarding_client_uidx
    ON recon_agent_onboarding (client_id);

-- Reload the PostgREST schema cache so the new table + unique index are visible to on_conflict immediately.
NOTIFY pgrst, 'reload schema';
