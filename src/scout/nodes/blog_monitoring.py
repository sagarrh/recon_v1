# blog_monitoring.py — LangGraph node: detect new competitor blog/case-study posts and spawn triggers.
# Purpose: Diffs feed+sitemap entries vs cached seen-set, classifies via Gemini, emits blog_detected InvestigationTriggers.
# Scope: Competitor collection, source inference, Claude classification, capping triggers per-cycle.
# Consumers: scout/graph.py runs this right after sov_detection; its triggers feed into merge_triggers.
import json
import logging
import re
from datetime import date, timedelta

from scout.config import get_config
from scout.db.cache import (
    get_new_feed_entries_since,
    get_new_sitemap_urls_since,
)
from scout.llm import call_synthesis, load_prompt
from scout.models.blog import BlogDetectionResult, BlogPost
from scout.models.sov import InvestigationTrigger
from scout.state import ScoutState

log = logging.getLogger(__name__)

CLASSIFY_BATCH_SIZE = 25
MAX_ENTRIES_PER_DOMAIN = 50


def blog_monitoring(state: ScoutState) -> dict:
    """LangFraph node: walk every competitor domain, classify newly-seen blog entries, and emit blog_detected triggers.
    Returns {'blog_detections': {domain: BlogDetectionResult}, 'blog_investigation_triggers': [trigger, ...]}."""
    config = get_config()
    if not config.blog_monitoring_enabled:
        return {"blog_detections": {}, "blog_investigation_triggers": []}

    sync_date = state.get("sync_date", str(date.today()))
    since_date = str(date.fromisoformat(sync_date) - timedelta(days=7))

    competitors = _collect_competitors(state)
    if not competitors:
        return {"blog_detections": {}, "blog_investigation_triggers": []}

    clusters = _collect_clusters(state)
    blog_detections: dict[str, BlogDetectionResult] = {}
    all_triggers: list[InvestigationTrigger] = []

    for domain, comp_info in competitors.items():
        print(f"[blog_monitoring] Processing {domain}")

        raw_entries = _get_db_entries(domain, since_date)

        if not raw_entries:
            print(f"[blog_monitoring] No new entries for {domain}")
            continue

        deduped = _deduplicate(raw_entries)
        print(f"[blog_monitoring] {len(deduped)} new entries for {domain}")

        classifications = _classify_with_claude(
            deduped, comp_info["competitor_name"], domain, clusters,
        )

        if not classifications:
            continue

        blog_posts = []
        for cls in classifications:
            url = cls.get("url") if isinstance(cls, dict) else None
            if not url:
                log.warning("[blog_monitoring] skipping malformed classification without URL")
                continue
            has_direct = any(
                r.get("relevance") == "direct"
                for r in cls.get("relevance_to_clusters", [])
            )
            source = _infer_source(url, raw_entries)
            raw_title = _find_raw_title(url, raw_entries)
            blog_posts.append(BlogPost(
                url=url,
                title=cls.get("title") or raw_title or cls.get("topic", ""),
                published_date=_find_published_date(url, raw_entries),
                content_type=cls.get("content_type", "other"),
                detection_source=source,
                competitor_domain=domain,
                competitor_name=comp_info["competitor_name"],
                relevance_to_clusters=cls.get("relevance_to_clusters", []),
            ))

            if has_direct and cls.get("content_type") in ("blog_post", "case_study"):
                for cluster_rel in cls.get("relevance_to_clusters", []):
                    if cluster_rel.get("relevance") != "direct":
                        continue
                    cid = cluster_rel.get("cluster_id")
                    if not cid:
                        continue
                    matched = next(
                        (cc for cc in comp_info["client_clusters"] if cc["cluster_id"] == cid),
                        None,
                    )
                    if matched is None:
                        log.warning(
                            "[blog_monitoring] skipping unowned cluster %s for %s",
                            cid,
                            domain,
                        )
                        continue
                    trigger = _build_trigger(
                        comp_info, matched, cluster_rel, cls, source,
                    )
                    all_triggers.append(trigger)

        detection_method = _determine_method(raw_entries)
        blog_detections[domain] = BlogDetectionResult(
            competitor_name=comp_info["competitor_name"],
            competitor_domain=domain,
            new_blog_posts=blog_posts,
            detection_method=detection_method,
            total_entries_scanned=len(deduped),
            new_entries_found=len(blog_posts),
            summary=f"{len(blog_posts)} new pages detected for {comp_info['competitor_name']}",
        )

    cap = config.max_blog_triggers_per_cycle
    if cap and cap > 0 and len(all_triggers) > cap:
        capped = all_triggers[:cap]
        dropped = [f"{t.competitor_name}::{t.cluster_id}" for t in all_triggers[cap:]]
        log.warning("[blog_monitoring] capped blog triggers from %d to %d; dropped: %s", len(all_triggers), cap, dropped)
    else:
        capped = all_triggers

    print(f"[blog_monitoring] Generated {len(capped)} blog investigation triggers")
    return {"blog_detections": blog_detections, "blog_investigation_triggers": capped}


def _collect_competitors(state: ScoutState) -> dict[str, dict]:
    """Flatten clients→clusters→competitors into {domain: {competitor_name, client_clusters[]}} for iteration.
    Aggregates a competitor's relevant client_clusters so trigger building knows which client to attribute to."""
    competitors = {}
    for client in state.get("clients", []):
        for cluster in client.get("clusters", []):
            for comp in cluster.get("competitors", []):
                domain = comp.get("competitor_domain", "")
                if not domain:
                    continue
                if domain not in competitors:
                    competitors[domain] = {
                        "competitor_name": comp["competitor_name"],
                        "competitor_domain": domain,
                        "client_clusters": [],
                    }
                competitors[domain]["client_clusters"].append({
                    "client_id": client["client_id"],
                    "client_name": client["client_name"],
                    "cluster_id": cluster["cluster_id"],
                    "cluster_label": cluster["cluster_label"],
                })
    return competitors


def _collect_clusters(state: ScoutState) -> list[dict]:
    """Flatten all unique clusters across clients into [{cluster_id, cluster_label}] for the classification prompt.
    Dedups by cluster_id so the LLM only sees each cluster once regardless of how many clients reference it."""
    clusters = []
    seen = set()
    for client in state.get("clients", []):
        for cluster in client.get("clusters", []):
            cid = cluster["cluster_id"]
            if cid not in seen:
                seen.add(cid)
                clusters.append({
                    "cluster_id": cid,
                    "cluster_label": cluster["cluster_label"],
                })
    return clusters


def _slug_to_title(url: str) -> str:
    """Convert the last URL path segment into a human-readable title (strip extension, replace dashes with spaces).
    Used when a sitemap URL carries no title so classifications still have something to show."""
    path = url.rstrip("/").split("/")[-1]
    path = re.sub(r"\.[a-z]{2,4}$", "", path)
    return re.sub(r"[-_]+", " ", path).strip()


def _get_db_entries(domain: str, since_date: str) -> list[dict]:
    """Load new-since-date feed and sitemap entries via the cache shim and tag each with _source='feed'/'sitemap'.
    Sitemap entries are back-filled with a _slug_to_title-derived title since sitemap-only URLs lack one; result capped at MAX_ENTRIES_PER_DOMAIN newest-first to keep classifier input bounded."""
    feed = get_new_feed_entries_since(domain, since_date)
    sitemap = get_new_sitemap_urls_since(domain, since_date)
    combined = []
    for e in feed:
        e["_source"] = "feed"
        combined.append(e)
    for s in sitemap:
        s["_source"] = "sitemap"
        s["title"] = _slug_to_title(s.get("url", ""))
        combined.append(s)
    combined.sort(
        key=lambda e: (e.get("published_date") or e.get("first_seen_at") or ""),
        reverse=True,
    )
    return combined[:MAX_ENTRIES_PER_DOMAIN]


def _deduplicate(entries: list[dict]) -> list[dict]:
    """Drop entries whose URL has already appeared earlier in the list, preserving first-seen order.
    Silently drops URL-less entries; used to avoid double-classifying the same URL from both feed and sitemap."""
    seen_urls = set()
    deduped = []
    for e in entries:
        url = e.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(e)
    return deduped


def _classify_with_claude(
    entries: list[dict],
    competitor_name: str,
    domain: str,
    clusters: list[dict],
) -> list[dict]:
    """Ask Gemini to classify candidate pages in CLASSIFY_BATCH_SIZE chunks so the JSON response stays under max_tokens.
    Returns the concatenated classifications across all successful batches; a single failed batch is logged and skipped, not fatal."""
    system_prompt = load_prompt("scout-blog-classification")
    pages = [
        {"url": e.get("url", ""), "title": e.get("title", ""), "summary": e.get("summary", "")}
        for e in entries
    ]
    if not pages:
        return []
    batches = [pages[i:i + CLASSIFY_BATCH_SIZE] for i in range(0, len(pages), CLASSIFY_BATCH_SIZE)]
    n = len(batches)
    all_classifications: list[dict] = []
    for i, batch in enumerate(batches, start=1):
        payload = {
            "competitor_name": competitor_name,
            "competitor_domain": domain,
            "pages": batch,
            "clusters": clusters,
        }
        user_message = (
            f"Classify the following pages.\n\n"
            f"{json.dumps(payload, indent=2)}\n\n"
            f"Return a JSON array with one classification object per page."
        )
        try:
            result = call_synthesis(
                system_prompt=system_prompt,
                user_message=user_message,
                expect_json=True,
                node_name="blog_monitoring",
                trigger_key=f"{domain}#batch{i}of{n}",
            )
        except Exception as e:
            print(f"[blog_monitoring] batch {i}/{n} for {domain} failed: {e}")
            continue
        if isinstance(result, list):
            batch_results = result
        elif isinstance(result, dict) and "classifications" in result:
            batch_results = result["classifications"] or []
        else:
            batch_results = []
        print(f"[blog_monitoring] batch {i}/{n} for {domain} returned {len(batch_results)} classifications")
        all_classifications.extend(batch_results)
    return all_classifications


def _infer_source(url: str, raw_entries: list[dict]) -> str:
    """Return the _source tag ('feed'/'sitemap'/'scrape') of the raw_entry whose url matches; default 'scrape'.
    Used on the classification objects so BlogPost.detection_source reflects provenance."""
    for e in raw_entries:
        if e.get("url") == url:
            return e.get("_source", "scrape")
    return "scrape"


def _find_raw_title(url: str, raw_entries: list[dict]) -> str:
    """Return the title from the raw_entry whose url matches; empty string when not found or title is missing.
    Used as a fallback when the classification object omits a title."""
    for e in raw_entries:
        if e.get("url") == url:
            return e.get("title", "") or ""
    return ""


def _find_published_date(url: str, raw_entries: list[dict]):
    """Return a date object parsed from the matching raw_entry's published_date or lastmod, else None.
    Tolerant of non-ISO strings — parse errors yield None rather than bubbling up."""
    for e in raw_entries:
        if e.get("url") == url:
            pd = e.get("published_date") or e.get("lastmod")
            if pd:
                try:
                    return date.fromisoformat(pd[:10])
                except (ValueError, TypeError):
                    pass
    return None


def _determine_method(raw_entries: list[dict]) -> str:
    """Classify the detection_method of a result bundle based on which _source tags appear: feed, sitemap, both, or scrape fallback.
    Result is recorded on BlogDetectionResult.detection_method for traceability/reporting."""
    sources = {e.get("_source") for e in raw_entries}
    if "feed" in sources and "sitemap" in sources:
        return "feed+sitemap"
    if "feed" in sources:
        return "feed"
    if "sitemap" in sources:
        return "sitemap"
    return "scrape_fallback"


def _build_trigger(
    comp_info: dict,
    client_info: dict,
    cluster_rel: dict,
    classification: dict,
    source: str,
) -> InvestigationTrigger:
    """Construct a blog_detected InvestigationTrigger carrying URL, title, and a human-readable triage reason.
    Priority is hard-coded to 'standard' since blog detections aren't ranked by SOV magnitude."""
    return InvestigationTrigger(
        client_id=client_info["client_id"],
        client_name=client_info["client_name"],
        competitor_name=comp_info["competitor_name"],
        competitor_domain=comp_info["competitor_domain"],
        cluster_id=cluster_rel["cluster_id"],
        cluster_label=cluster_rel["cluster_label"],
        shift_type="blog_detected",
        shift_magnitude=0.0,
        correlated_displacement=False,
        investigation_priority="standard",
        triage_reason=(
            f"New {classification.get('content_type', 'blog_post')} detected: "
            f"'{classification.get('topic', 'unknown')}' at {classification.get('url', '')} "
            f"— direct relevance to cluster '{cluster_rel['cluster_label']}'. "
            f"Detected via {source}."
        ),
        blog_post_url=classification.get("url"),
        blog_post_title=classification.get("topic"),
        detection_source=source,
    )
