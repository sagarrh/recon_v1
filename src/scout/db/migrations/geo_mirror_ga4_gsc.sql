-- geo_mirror_ga4_gsc.sql — Align the GA4/GSC GEO-mirror tables in the agentic DB so scout-sync can copy them.
-- These mirrors were declared with `id BIGINT IDENTITY` and no `updated_at`, but the GEO source uses `id UUID`
-- + `updated_at`. The mirror pattern copies the source id and upserts ON CONFLICT (id), with updated_at as the
-- incremental cursor — so both must match the source. The tables have never been synced (empty), so a
-- DROP + recreate is safe. RLS re-enabled with NO policies (cron/service-key only), matching agent_db_geo_schema.
DROP TABLE IF EXISTS public.ga4_metrics;
DROP TABLE IF EXISTS public.gsc_query_page_metrics;

CREATE TABLE public.gsc_query_page_metrics (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- copied from the GEO source id (upsert on_conflict=id)
    site_url     TEXT,
    metric_date  DATE,
    query        TEXT,
    page         TEXT,
    clicks       BIGINT,
    impressions  BIGINT,
    ctr          NUMERIC,
    position     NUMERIC,
    country      TEXT,
    device       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ                                    -- scout-sync incremental cursor
);

CREATE TABLE public.ga4_metrics (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- copied from the GEO source id (upsert on_conflict=id)
    property_id       TEXT,
    metric_date       DATE,
    source            TEXT,
    medium            TEXT,
    campaign          TEXT,
    landing_page      TEXT,
    country           TEXT,
    device            TEXT,
    sessions          BIGINT,
    engaged_sessions  BIGINT,
    total_users       BIGINT,
    new_users         BIGINT,
    conversions       NUMERIC,
    revenue           NUMERIC,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ                                   -- scout-sync incremental cursor
);

-- RLS: deny anon/authenticated; cron + service key bypass. No policies on purpose.
ALTER TABLE public.gsc_query_page_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ga4_metrics            ENABLE ROW LEVEL SECURITY;

-- Bridge-filter indexes (same as agent_db_geo_schema.sql PART 3).
CREATE INDEX IF NOT EXISTS idx_gsc_site_date ON public.gsc_query_page_metrics (site_url, metric_date DESC);
CREATE INDEX IF NOT EXISTS idx_gsc_query     ON public.gsc_query_page_metrics (site_url, query);
CREATE INDEX IF NOT EXISTS idx_ga4_prop_date ON public.ga4_metrics (property_id, metric_date DESC);
CREATE INDEX IF NOT EXISTS idx_ga4_landing   ON public.ga4_metrics (property_id, landing_page);

-- Reload the PostgREST schema cache so the recreated tables are visible to the Scout API immediately.
NOTIFY pgrst, 'reload schema';
