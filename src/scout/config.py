# config.py — Typed runtime configuration for the Scout agent.
# Purpose: Centralizes all environment-driven settings (API keys, thresholds, feature flags) via pydantic-settings.
# Scope: Loads from process env + .env file; exposes a memoized ScoutConfig singleton via get_config().
# Consumers: run.py bootstrap, llm.py clients, db/* connectors, nodes/* thresholds, integrations/* API keys.
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoutConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bright_data_api_key: str = ""
    bright_data_web_unlocker_zone: str = "web_unlocker1"
    bright_data_serp_zone: str = "serp_api1"
    bright_data_serp_timeout_seconds: int = 60  # SERP read timeout
    bright_data_serp_max_attempts: int = 2  # SERP total tries = 1 retry
    bright_data_scrape_timeout_seconds: int = (
        120  # Web Unlocker scrape read timeout (doubled from the old hardcoded 60 for slow sites)
    )
    bright_data_scrape_max_attempts: int = (
        3  # scrape total tries = 1 try + 2 retries before website_diff falls back
    )
    supabase_url: str = ""
    supabase_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )
    log_level: str = "INFO"
    news_mode: bool = True
    detection_threshold_sd: float = 2.0
    displacement_threshold_sd: float = 1.5
    new_entrant_min_citations: int = 3
    # R3-1 — deterministic per-source minimum-evidence floors checked BEFORE any investigation LLM call.
    website_min_changed_elements: int = (
        2  # min (sentence churn + new pages + schema + themes) before website_diff calls the LLM
    )
    web_intel_min_serp_rows: int = (
        1  # min SERP rows with a non-empty snippet before web_intelligence calls the LLM
    )
    ai_resp_min_paired_weeks: int = (
        1  # min paired current/baseline weeks before ai_response_analysis calls the LLM
    )
    # R3-2 — enrich-first collection: when a source trips its floor, widen collection before abstaining.
    enrichment_enabled: bool = True  # kill-switch for the enrich-before-abstain path
    website_enrich_max_pages: int = (
        60  # widened sitemap page cap during website enrichment (base is 20)
    )
    ai_resp_enrich_weeks: int = (
        12  # widened AI-response window (weeks) during ai_response enrichment (base is 3)
    )
    min_abstained_for_gap: int = 2  # R3-3: >= this many abstained sources → name them in gap_analysis + cap confidence at low
    max_investigations_per_client: int = (
        0  # R0-6: 0 = unlimited (cost is not a constraint); per-client investigation cap lifted
    )
    flat_baseline_threshold_pp: float = 3.0
    sov_literal_matching_enabled: bool = False
    sov_literal_matching_shadow_enabled: bool = True
    aivc_recon_legacy_ai_analysis_enabled: bool = False
    rolling_window_weeks: int = 4
    graduation_threshold_weeks: int = 4  # R4-1: a competitor with >= this many CONSECUTIVE SOV weeks graduates out of news_mode into the z-score/abs-floor path
    regraduation_null_weeks: int = 2  # R4-4: a graduated cluster re-enters news_mode only after this many consecutive null weeks (single null week stays graduated)
    # TFS-08 — absolute-pp triage severity thresholds (news_mode has no z-score baseline).
    # R0-7: these four floors are EXACT configured thresholds (not approximate); bump severity_floor_version on any change.
    min_floor_pp: float = 2.0
    win_pp: float = 5.0
    crit_loss_pp: float = 15.0
    established_pp: float = 10.0
    severity_floor_version: str = "v1"  # R0-7: floor-set version stamped onto scout_decision_log; bump when any floor above changes
    # TFS-09 — when True, NOISE clusters get BOTH a digest line AND a full report (run this way until
    # the noise classifier is trusted); set False to collapse NOISE clusters to the digest line only.
    noise_full_report: bool = True
    quarantine_rate_alert_threshold: float = 0.25  # R1-4: quarantine_count/total_artifacts above this folds a line into the cycle-issues alert
    numeric_provenance_shadow_mode: bool = True  # R2-4: master kill — True = record hallucinated-number notes/metrics only (forces both enforce flags off)
    numeric_provenance_client_enforce: bool = False  # shadow off + this: rewrite unbacked client-summary numbers to qualitative "some" (graceful, never zeros)
    numeric_provenance_internal_enforce: bool = False  # shadow off + this: quarantine internal reports with an unbacked number (can zero a thin cycle — enable only after a low measured rate)
    claim_provenance_enabled: bool = True  # measure unbacked_claim_rate (named events not in collected evidence); shadow-only unless enforce is on
    claim_provenance_enforce: bool = False  # enforce on: qualify unbacked named specifics in the client summary (never a backed/cited claim)
    calibration_feedback_enabled: bool = False  # R5-4: OFF by default — when True, nudge rec confidence toward the measured cause-class recovery prior
    calibration_min_samples: int = 20  # R5-4: a cause class needs >= this many executed outcomes before its prior can move confidence
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v4-pro"  # extraction model via OpenRouter; swap to "deepseek/deepseek-v4-flash" for the V4-Flash test
    extraction_provider: str = "deepseek"  # OpenRouter provider slug pinning extraction to the DeepSeek endpoint (provider.order needs the slug, not the display name); empty disables the pin
    extraction_self_consistency_n: int = (
        1  # R0-4: deterministic self-consistency samples for extraction (1 = off)
    )
    gemini_model: str = (
        "moonshotai/kimi-k2.6"  # synthesis model via OpenRouter — Kimi K2.6 (pinned to W&B below)
    )
    gemini_provider: str = "wandb"  # OpenRouter provider slug pinning synthesis to the Weights & Biases endpoint (tag wandb/fp4; provider.order needs the slug, not the display name); empty disables the pin
    gemini_max_tokens: int = 20000
    gemini_retry_delay_seconds: int = 2
    llm_timeout_seconds: int = 240
    llm_max_concurrency: int = (
        3  # cap concurrent OpenRouter calls across the LangGraph fan-out (429 mitigation)
    )
    llm_max_retries: int = (
        6  # OpenRouter/OpenAI SDK retries (exp backoff + honors Retry-After) for 429/5xx
    )
    llm_allow_provider_fallback: bool = (
        False  # opt-in: let OpenRouter route around a rate-limited pinned provider
    )
    capture_reasoning: bool = (
        True  # request + persist model reasoning (reasoning_content) to LangSmith + prompt_log
    )
    client_summary_min_chars: int = 120  # R1-3: below this a client summary is retried once then deterministically templated (sub-min floor, distinct from the 3500 hard cap)
    token_trailing_multiple_alert: float = 2.3  # R1-6: alert when this run's total_tokens exceeds this multiple of the trailing average
    token_trailing_window_runs: int = (
        4  # R1-6: number of prior completed runs in the trailing-token average
    )
    evidence_summarization_enabled: bool = False  # R0-1: LLM evidence compression disabled — full evidence reaches synthesis (cost is not a constraint)
    summarizer_model: str = "google/gemini-2.5-flash-lite"  # cheap model for evidence compression (plain OpenRouter routing)
    supabase_sync_on_run: bool = True
    langsmith_tracing: bool = True
    langsmith_api_key: str = ""
    langsmith_project: str = "scout-agent"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    blog_monitoring_enabled: bool = True
    max_blog_triggers_per_cycle: int = 0  # R0-6: 0 = unlimited; per-cycle blog-trigger cap lifted
    blog_first_scan_lookback_days: int = 14
    feed_discovery_timeout_seconds: int = 30
    sitemap_max_child_sitemaps: int = 5
    sitemap_max_urls: int = 5000
    slack_enabled: bool = True
    slack_intel_webhook_url: str = ""
    slack_alerts_webhook_url: str = ""
    # GEO reuse bridge — read-only consumption of GEO tables synced into the Scout DB.
    geo_report_data_sov_enabled: bool = True  # Stream 1: report_data SOV rung (reader rung 3)
    geo_cluster_vis_sov_enabled: bool = False  # Stream 1b: ephemeral cluster_visibility_scores rung
    geo_cluster_registry_enabled: bool = (
        True  # Stream 2: registry-only clusters (zero-data, news_mode)
    )
    geo_scraped_cache_enabled: bool = True  # Stream 3: reuse scraped cache + website_files + schema
    geo_financials_enabled: bool = True  # Stream 4a: revenue framing in recommendation_gen
    geo_comentions_enabled: bool = True  # Stream 4b: co-mention signal into web_intelligence
    geo_client_readiness_enabled: bool = (
        True  # Stream 3b: client-site AI-readiness node (website_files + schema)
    )
    geo_scraped_cache_ttl_days: int = 14  # company_scraped_data_cache staleness gate
    # Deep historical, whole-field recommendation synthesis (gated; default OFF = today's per-primary path).
    deep_recommendation_enabled: bool = (
        False  # master switch for the deep historical whole-field synthesis path
    )
    deep_recommendation_lookback_weeks: int = 12  # week bound for the per-cluster history readers
    deep_recommendation_max_tokens: int = 80000  # synthesis budget for the (much larger) deep call
    deep_recommendation_model: str = ""  # empty = reuse gemini_model (Kimi K2.6); set to override
    # Tier 1 revenue layer — all default OFF; flags-off = byte-identical output to today.
    revenue_layer_enabled: bool = False
    geo_gsc_enabled: bool = False
    geo_ga4_enabled: bool = False
    geo_crm_enabled: bool = False
    revenue_outcome_enabled: bool = False
    revenue_calibration_feedback_enabled: bool = False
    revenue_ai_referral_capture_fraction: float = 0.15
    revenue_coefficient_version: str = "rev_v1"
    # Tier 2 — per-asset dollar attribution (all default OFF; flags-off = byte-identical to today).
    asset_attribution_enabled: bool = False  # Tier-2 master (with revenue_layer_enabled)
    geo_content_tracking_enabled: bool = (
        False  # Phase B: read content_tracking mirror (existing/built assets)
    )
    geo_blog_generations_enabled: bool = (
        False  # Phase B: read blog_generations mirror (asset->cluster bridge)
    )
    asset_target_derivation_enabled: bool = (
        False  # Phase A: ship-time target-asset registration in sed_writer
    )
    revenue_asset_surfacing_enabled: bool = (
        False  # internal-report attributed line (compute-and-store silently first)
    )
    asset_attribution_channel_weights: dict = {
        "ga4_landing_page": 1.0,
        "selection_events": 0.7,
        "attribution_events": 0.5,
        "tier1_modeled": 0.4,
    }
    asset_modeled_discount: float = 0.6  # confidence multiplier for modeled-basis rows
    crm_lag_penalty_days: int = 90  # days over which lag_penalty decays to its floor
    crm_lag_penalty_floor: float = 0.4  # minimum lag_penalty for long-lag CRM dollars
    # Tier 3 — Asset Builder / Execution Arm (all default OFF; separate CLI surface, never a graph node).
    asset_builder_enabled: bool = (
        False  # MASTER — off = every Tier-3 CLI short-circuits before any DB read
    )
    builder_brief_intake_enabled: bool = (
        False  # intake: scout_assets target rows -> scout_build_briefs
    )
    builder_generate_enabled: bool = False  # generation orchestrator
    builder_schema_gen_enabled: bool = False  # per-generator: JSON-LD
    builder_llms_gen_enabled: bool = False  # per-generator: llms.txt
    builder_aibots_gen_enabled: bool = False  # per-generator: ai_bots / robots directives
    builder_llms_prose_enabled: bool = False  # LLM prose fill (off = deterministic skeleton only)
    builder_fact_enrichment_enabled: bool = (
        False  # Bright Data re-scrape to ground unbacked prose numbers
    )
    builder_internal_gate_enabled: bool = False  # approve_asset.py internal gate
    builder_client_approval_required: bool = True  # POLICY: external client gate mandatory (per-run overridable; unreachable while master off)
    builder_deploy_handoff_enabled: bool = False  # emit hand-off packages
    builder_verify_enabled: bool = False  # post-hand-off verification reads
    builder_attribution_link_enabled: bool = (
        False  # let Tier-2 attribution join verified built assets
    )
    builder_facts_recon_enabled: bool = (
        False  # recon agent: scrape client site -> extract facts -> recon_agent_onboarding
    )
    recon_profile_ttl_days: int = (
        30  # recon_agent_onboarding staleness gate (refresh profiles older than this)
    )
    recon_facts_max_pages: int = (
        4  # client pages scraped per recon run (home + top solution/product pages)
    )
    recon_facts_deep: bool = (
        False  # recon v2: multi-page crawl + SERP page discovery (vs homepage-only)
    )
    recon_identity_enabled: bool = False  # recon: SERP find_identity_urls -> schema.org sameAs
    recon_reviews_enabled: bool = (
        False  # recon: SERP+scrape find_reviews -> grounded AggregateRating (D12-safe)
    )
    evidence_grounding_enabled: bool = (
        False  # report: Bright Data find_competitor_events grounds probable_cause
    )
    grounding_serp_max: int = 3  # max competitor-event results grounded per verdict
    client_gap_recommendations_enabled: bool = (
        False  # derive+store client-side GEO gap recommendations at recon time
    )
    gap_competitive_enabled: bool = (
        False  # competitive-parity gaps (extra Bright Data calls per client)
    )
    gap_competitive_max: int = 2  # max competitors probed for parity signals per client
    gap_competitive_narrative_enabled: bool = (
        False  # grounded LLM parity narrative over collected competitor signals
    )
    gap_competitive_branches: int = (
        3  # ToT: diverse candidate assessments generated per client (lens-driven)
    )
    gap_competitive_judge_enabled: bool = (
        True  # run the separate judge/prune call (off -> deterministic fallback rank)
    )
    gap_competitive_aggregate_top_k: int = (
        2  # survivors fed to the aggregate merge (cost bound on final synthesis)
    )
    asset_max_generations_per_run: int = 10  # hard cap per CLI invocation
    asset_verification_max_cycles: int = 4  # failed verify attempts before a loud not-live warning


_config = None


def get_config() -> ScoutConfig:
    """Return the process-wide ScoutConfig singleton, lazily constructing it on first access.
    First call reads env + .env; subsequent calls are O(1) and yield the same instance."""
    global _config
    if _config is None:
        _config = ScoutConfig()
    return _config
