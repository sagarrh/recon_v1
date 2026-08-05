# sed_mapping.py — Supabase (SED) table + column name mapping constants.
# Purpose: Centralizes the SED table names and field aliases so renames upstream are a one-file change.
# Scope: Constants only; no functions, no logic.
# Consumers: scout/db/reader.py + sov_compute.py (read), scout/db/sed_writer.py (write), scripts/collect_feeds.py + diagnose_client.py.
CLIENT_TABLE = "clients"
CLIENT_COLS = {
    "client_id": "client_id",
    "client_name": "client_name",
    "company_domain": "company_domain",
    "company_website": "company_website",
    "competitors": "competitors",
    "competitors_url": "competitors_url",
}

SOV_TABLE = "sov_weekly"
SOV_COLS = {
    "client_id": "client_id",
    "cluster_id": "cluster_id",
    "cluster_name": "cluster_name",
    "top_companies": "top_companies",
    "week_date": "week_date",
    "created_at": "synced_at",
}
TOP_COMPANIES_SCORE_FIELD = "total_visibility"

AI_MONITORING_TABLE = "ai_responses"
AI_MONITORING_COLS = {
    "client_id": "client_id",
    "cluster_id": "cluster_id",
    "cluster_name": "cluster_name",
    "platform": "platform",
    "query": "query",
    "answers_list": "answers_list",
    "citations_data": "citations_data",
    "created_at": "synced_at",
}

SOV_LOOKBACK_WEEKS = 6
AI_LOOKBACK_WEEKS = 3

# GEO mirror tables (read-only). Synced into the Scout DB by the 30-min GEO->Scout cron.
# Scout reads these directly via get_sed_client(); the bridge NEVER writes to them.
REPORT_DATA_TABLE = "report_data"
REPORT_DATA_COLS = {
    "client_id": "client_id",            # TEXT here (NOT uuid) — pass client_id as a string to .eq()
    "ai_visibility_data": "ai_visibility_data",
    "website_files_data": "website_files_data",
    "schema_data": "schema_data",
    "company_website": "company_website",
    "created_at": "created_at",
}
QUERIES_GROUPS_TABLE = "queries_groups_data"
QUERIES_GROUPS_COL = "queries_groups_data"     # jsonb {domain:{region:{"0":{"Queries":[...]}}}}
KEYWORDS_GROUPS_TABLE = "keywords_groups_data"
KEYWORDS_GROUPS_COL = "keywords_groups_data"   # jsonb {domain:{region:{"0":{"Keywords":[...]}}}}
COMPANY_SCRAPED_CACHE_TABLE = "company_scraped_data_cache"   # url-keyed, shared across clients
SELECTION_EVENTS_TABLE = "selection_events"
ONBOARDING_TABLE = "onboarding"
USER_METRICS_TABLE = "user_metrics"
# Financials allow-list — selected explicitly so a future edit can never widen the SELECT
# onto ga4_refresh_token / gsc_refresh_token (secrets) in onboarding / user_metrics.
ONBOARDING_FINANCIAL_COLS = ["average_order_value", "conversion_rate", "estimated_ctr", "currency"]

# report_data is a durable point-in-time snapshot (sparse over time), so its window is wider
# than SOV_LOOKBACK_WEEKS=6.
REPORT_DATA_LOOKBACK_WEEKS = 12

# Write-side tables (Scout pipeline -> SED). One artifact type per table.
SCOUT_CYCLE_RUNS_TABLE = "cycle_runs"
SCOUT_RECOMMENDATIONS_TABLE = "recommendations"
SCOUT_REPORTS_TABLE = "reports"
SCOUT_SOV_TRACKING_TABLE = "sov_tracking"
SCOUT_INVESTIGATION_TRIGGERS_TABLE = "investigation_triggers"
SCOUT_INVESTIGATIONS_TABLE = "investigations"
SCOUT_BLOG_DETECTIONS_TABLE = "blog_detections"
SCOUT_DECISION_LOG_TABLE = "scout_decision_log"   # TFS-11: per-run triage decision audit
# R1-5: per-recommendation outcome lifecycle + ship-time SOV baseline snapshot.
# R5-1 REUSES this constant and only appends ADD COLUMN IF NOT EXISTS to scout_outcomes.sql — never re-declares.
SCOUT_OUTCOMES_TABLE = "scout_outcomes"

# Owned by the aivc CLI (migrations/0008), read here. Carries implemented_at — the ONLY legitimate
# anchor for an outcome window. Same Supabase project, so PostgREST reaches it like any other table.
AIVC_ACTION_EXECUTIONS_TABLE = "aivc_action_executions"
AIVC_EXECUTION_READ_COLS = [
    "subject_id", "client_id", "status", "implemented_at", "implemented_by",
    "target_pages", "target_queries", "action_type", "verification_status",
]
# Statuses that mean the change actually shipped. Anything else is not measurable.
EXECUTED_STATUSES = ("executed", "verified")

# R1-1: sync_state heartbeat written per-stream by the scout-sync edge fn (R1-2); read by the reader's freshness gate.
SYNC_STATE_TABLE = "sync_state"
SYNC_STATE_COLS = {
    "table_name": "table_name",
    "last_synced_at": "last_synced_at",
}

# GEO revenue mirror tables (read-only, per-client; Tier-1 Phase B). Synced by scout-sync every 30 min.
# Vendor CRM (crm_deals/crm_leads) is NOT mirrored/consumed — it is Multiplier AI's OWN sales pipeline
# (no client_id) and must never feed client revenue attribution.
GSC_QPM_TABLE = "gsc_query_page_metrics"
GA4_METRICS_TABLE = "ga4_metrics"
ATTRIBUTION_EVENTS_TABLE = "attribution_events"

# Join bridge — user_metrics carries the GSC site + GA4 property handles.
USER_METRICS_JOIN_COLS = ["client_id", "gsc_site_url", "ga4_property_id"]
# Revenue-read allow-lists: SELECT only these; NEVER widen onto ga4_refresh_token/gsc_refresh_token.
GA4_METRICS_READ_COLS = ["metric_date", "landing_page", "source", "medium",
                         "sessions", "conversions", "revenue"]
GSC_QPM_READ_COLS = ["metric_date", "query", "page", "clicks", "impressions",
                     "ctr", "position"]

# Tier 2 — asset registry + attribution tables (Scout-owned, write-side).
SCOUT_ASSETS_TABLE = "scout_assets"
SCOUT_ASSET_ATTRIBUTION_TABLE = "scout_asset_attribution"

# Recon agent — Scout-owned client facts-of-record. Populated by scout/builders/facts_recon.py
# (scrape client site -> LLM extract -> upsert), read by scout/db/client_context.get_client_profile
# to un-starve the Tier-3 asset builder. Keyed 1:1 to onboarding.client_id.
RECON_AGENT_ONBOARDING_TABLE = "recon_agent_onboarding"
# Read allow-list — SELECT only these (never widen onto a future secret column).
RECON_PROFILE_COLS = ["client_id", "company_name", "domain", "description", "products",
                      "services", "service_type", "area_served", "differentiators",
                      "same_as", "logo_url", "rating_value", "review_count", "rating_source",
                      "gaps", "contact_point", "provenance", "source", "confidence", "refreshed_at"]

# GEO content mirror tables (read-only; Tier-2 Phase B). Synced by scout-sync when the mirror lands.
# Explicit allow-lists — never widen onto generation_metadata secrets or bulk blog_content.
CONTENT_TRACKING_TABLE = "content_tracking"
CONTENT_TRACKING_COLS = {
    "client_id": "client_id", "content_url": "content_url", "content_title": "content_title",
    "content_type": "content_type", "published_date": "published_date",
    "generated_by_vibe_engine": "generated_by_vibe_engine",
}
BLOG_GENERATIONS_TABLE = "blog_generations"
BLOG_GENERATIONS_COLS = {
    "client_id": "client_id", "title": "title", "queries": "queries", "keywords": "keywords",
    "seo_metadata": "seo_metadata", "content_quality_score": "content_quality_score",
}

# Tier 3 — asset builder tables (Scout-owned, write-side).
SCOUT_BUILD_BRIEFS_TABLE = "scout_build_briefs"
SCOUT_ASSET_APPROVALS_TABLE = "scout_asset_approvals"
