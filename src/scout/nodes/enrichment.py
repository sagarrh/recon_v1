# enrichment.py — Shared enrich-first collection helpers invoked when an investigation source trips its evidence floor.
# Purpose: Widen evidence collection (broader SERP set, wider sitemap pull, more AI-response weeks) before a source abstains (R3-2).
# Scope: Orchestrates the same integrations the nodes use, gated by enrichment_enabled; no LLM judgment, deterministic widening only.
# Consumers: scout/nodes/web_intelligence.py, website_diff.py, ai_response_analysis.py — called only on a below-floor trigger.
from scout.config import get_config
from scout.db.cache import get_ai_response_bundle
from scout.integrations.bright_data import serp_search_enriched


def enrich_web_intel(trigger) -> dict:
    """Re-run SERP collection with the broader enrichment template set; returns the enriched {queries_run, serp_results}.
    Widens beyond the base shift-type queries so a thin web-intelligence trigger can clear its floor before abstaining."""
    print(f"[enrichment] {trigger.competitor_name}::{trigger.cluster_id} widening SERP (enriched template set)")
    return serp_search_enriched(trigger.competitor_name, trigger.cluster_label, trigger.shift_type)


def enrich_website(trigger, known_urls: set | None = None, seen_ids: set | None = None) -> tuple[list, list]:
    """Re-pull the competitor sitemap (wider page cap) and feed titles; returns (new_pages, content_themes).
    known_urls/seen_ids are the caller's run-start baseline snapshot, passed through so this re-call diffs against the same set the first detection did (the helpers persist as they go).
    Widens the website-diff structural signal so a thin trigger can clear its floor before abstaining (schema is HTML-derived, unchanged)."""
    from scout.nodes.website_diff import _get_feed_titles, _get_new_sitemap_pages
    cfg = get_config()
    print(f"[enrichment] {trigger.competitor_name}::{trigger.cluster_id} widening sitemap to {cfg.website_enrich_max_pages} pages")
    new_pages = _get_new_sitemap_pages(trigger.competitor_domain, max_pages=cfg.website_enrich_max_pages, known_urls=known_urls)
    content_themes = _get_feed_titles(trigger.competitor_domain, seen_ids=seen_ids)
    return new_pages, content_themes


def enrich_ai_responses(trigger, sync_date) -> dict:
    """Re-fetch the AI-response bundle over a wider week window so a >=4-day-earlier baseline is more likely found.
    Returns the (possibly still one-sided) bundle; the caller re-counts paired weeks against the floor (R3-2)."""
    cfg = get_config()
    print(f"[enrichment] {trigger.competitor_name}::{trigger.cluster_id} widening AI-response window to {cfg.ai_resp_enrich_weeks}w")
    return get_ai_response_bundle(trigger.cluster_id, sync_date, weeks=cfg.ai_resp_enrich_weeks) or {}
