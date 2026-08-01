# website_diff.py — LangGraph node: competitor website change analysis via scrape+diff+pre-fetch.
# Purpose: Combines Bright Data scrape, sitemap pre-fetch, feed pre-fetch, and Grok-driven diff analysis into WebsiteChanges.
# Scope: Schema extraction, sentence-level diff, pre-fetch shortcut when HTML is stable, fallback on API failures.
# Consumers: scout/graph.py registers this as website_diff_analysis; recommendation_gen consumes the resulting dict.
import json
import re

from scout.config import get_config
from scout.db.cache import get_geo_scraped_page, get_prev_snapshot, store_snapshot
from scout.evidence_floor import below_floor, website_changed_elements
from scout.integrations.bright_data import scrape_website
from scout.keys import make_trigger_key
from scout.llm import call_extraction_consistent, load_prompt
from scout.models.investigation import WebsiteChanges
from scout.nodes.enrichment import enrich_website
from scout.state import ScoutState
from scout.utils import is_transient

_SCHEMA_TYPE_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_FALLBACK_WEBSITE_CHANGES = {
    "new_pages": [],
    "modified_pages": [],
    "schema_added": [],
    "content_themes": [],
    "cluster_relevance": "none",
    "summary": "No website diff data available — evidence source unavailable for this trigger.",
    "confidence": "none",
}


def website_diff_analysis(state: ScoutState) -> dict:
    """LangGraph node: build a WebsiteChanges per trigger by diffing the current scrape vs the last snapshot + pre-fetches.
    Takes a pre-fetch-only shortcut when the HTML hasn't materially changed but sitemap/schema/feed additions exist."""
    system_prompt = load_prompt("scout-website-diff-analysis")
    triggers = state["investigation_triggers"]
    website_changes: dict[str, WebsiteChanges] = {}
    key_domain: dict[str, str] = {}   # trigger key -> competitor_domain, for post-loop AI-access stamping

    for trigger in triggers:
        key = make_trigger_key(trigger.client_id, trigger.competitor_name, trigger.cluster_id)
        key_domain[key] = trigger.competitor_domain
        print(f"[website_diff] Processing {key}")

        try:
            current = get_geo_scraped_page(trigger.competitor_domain) or scrape_website(trigger.competitor_domain)
            previous = get_prev_snapshot(trigger.competitor_domain)
            store_snapshot(
                trigger.competitor_domain,
                state["sync_date"],
                current,
                crawl_metadata={"scraped_at": current.get("scraped_at", "")},
                run_id=state.get("run_id"),
            )
            scrape_bundle = {"current": current, "previous": previous}
        except Exception as e:
            print(f"[website_diff] scrape failed for {trigger.competitor_domain}: {e}")
            scrape_bundle = None

        if scrape_bundle is None:
            website_changes[key] = _fallback(trigger.competitor_name, trigger.cluster_id)
            continue

        current_scrape = scrape_bundle.get("current", {})
        current_html = current_scrape.get("content", "")
        previous = scrape_bundle.get("previous")

        schema_added = _extract_schema_types(current_html)

        # Snapshot the baseline once per domain so both the display call and enrichment's re-call diff
        # against the same run-start set — the detection helpers persist as they go, so re-reading the DB
        # would show this run's own writes and zero out legitimately-new pages.
        from scout.db.cache import get_known_sitemap_urls, get_seen_entry_ids
        known_sitemap = get_known_sitemap_urls(trigger.competitor_domain)
        seen_feed_ids = get_seen_entry_ids(trigger.competitor_domain)

        new_pages = _get_new_sitemap_pages(trigger.competitor_domain, known_urls=known_sitemap)
        content_themes = _get_feed_titles(trigger.competitor_domain, seen_ids=seen_feed_ids)

        prev_text = previous.get("content", "") if previous else ""
        page_diff = compute_page_diff(prev_text, current_html) if previous else None

        if page_diff and not page_diff["materially_changed"] and (new_pages or schema_added or content_themes):
            print(f"[website_diff] {key} — pre-fetch only (no material HTML diff)")
            website_changes[key] = _build_from_prefetch(
                trigger.competitor_name, trigger.cluster_id,
                new_pages, schema_added, content_themes,
            )
            continue

        # R3-1/R3-2: deterministic minimum-evidence floor — below it, enrich first, then skip the LLM
        # (no prompt_log row) only when enrichment still can't clear the floor.
        changed = website_changed_elements(page_diff, new_pages, schema_added, content_themes)
        if below_floor(changed, get_config().website_min_changed_elements):
            if get_config().enrichment_enabled:
                new_pages, content_themes = enrich_website(trigger, known_sitemap, seen_feed_ids)
                changed = website_changed_elements(page_diff, new_pages, schema_added, content_themes)
            if below_floor(changed, get_config().website_min_changed_elements):
                print(f"[website_diff] {key} — below evidence floor ({changed} changed elements) after enrichment, abstaining")
                ab = _fallback(trigger.competitor_name, trigger.cluster_id)
                ab.abstained = True
                ab.abstention_counts = {"changed_elements": changed}
                website_changes[key] = ab
                continue

        diff_context = _build_diff_context(current_scrape, previous, page_diff)
        user_message = _build_user_message(trigger, diff_context, new_pages, schema_added, content_themes)

        raw = _call_with_fallback(
            system_prompt, user_message, trigger.competitor_name, trigger.cluster_id, key
        )
        website_changes[key] = raw

    # Stamp each competitor's LIVE AI-access files (llms.txt/robots/ai-bots) onto its WebsiteChanges,
    # deduped by domain — only when client readiness is on (the comparison's sole consumer). {} = not checked / unreachable.
    if get_config().geo_client_readiness_enabled:
        from scout.integrations.ai_access import fetch_ai_access_files
        ai_cache: dict[str, dict] = {}
        for k, wc in website_changes.items():
            dom = key_domain.get(k, "")
            if dom not in ai_cache:
                try:
                    ai_cache[dom] = fetch_ai_access_files(dom) or {}
                except Exception as e:
                    print(f"[website_diff] AI-access fetch failed for {dom}: {e}")
                    ai_cache[dom] = {}
            wc.ai_access = ai_cache[dom]

    return {"website_changes": website_changes}


def _extract_schema_types(html: str) -> list[str]:
    """Scan HTML for <script type='application/ld+json'> blocks and return a sorted unique list of @type values.
    Silently skips malformed JSON blocks so a single broken LD+JSON script doesn't break extraction."""
    types = set()
    for match in _SCHEMA_TYPE_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    t = item.get("@type")
                    if t:
                        types.add(str(t))
        except (json.JSONDecodeError, TypeError):
            continue
    return sorted(types)


def _get_new_sitemap_pages(domain: str, max_pages: int = 20, known_urls: set[str] | None = None) -> list[dict]:
    """Pre-fetch: pull the sitemap, keep blog-like URLs, diff against known_urls, persist the full new set, return up to max_pages new {url, lastmod}.
    known_urls is the run-start baseline snapshot; when None it is fetched here (so the diff and the persist stay against the same set even though enrichment calls this twice).
    Returns [] on any error so website_diff still runs even when the sitemap harvester fails (R3-2 raises max_pages on enrichment)."""
    try:
        from scout.db.cache import get_known_sitemap_urls, store_sitemap_urls
        from scout.integrations.sitemap_parser import (
            detect_new_sitemap_urls,
            fetch_sitemap,
            filter_blog_urls,
        )
        all_urls = fetch_sitemap(domain)
        blog_entries = filter_blog_urls(all_urls, domain)
        current_urls = [e["url"] for e in blog_entries]
        if known_urls is None:
            known_urls = get_known_sitemap_urls(domain)
        new_urls = detect_new_sitemap_urls(current_urls, known_urls)
        url_map = {e["url"]: e for e in blog_entries}
        new_entries = [url_map[u] for u in new_urls if u in url_map]
        if new_entries:
            store_sitemap_urls(domain, new_entries)
        return [{"url": u, "lastmod": url_map.get(u, {}).get("lastmod")} for u in new_urls[:max_pages]]
    except Exception as e:
        print(f"[website_diff] sitemap pre-fetch failed for {domain}: {e}")
        return []


def _get_feed_titles(domain: str, seen_ids: set[str] | None = None) -> list[str]:
    """Pre-fetch: discover feed URL, parse it, filter to unseen entries, persist them, return up to 10 non-empty titles.
    seen_ids is the run-start baseline snapshot; when None it is fetched here (same reason as _get_new_sitemap_pages).
    Returns [] on discovery/fetch/parse error so website_diff still proceeds without feed-derived themes."""
    try:
        from scout.db.cache import get_seen_entry_ids, mark_entries_seen
        from scout.integrations.feed_parser import discover_feed_url, filter_new_entries, parse_feed
        feed_info = discover_feed_url(domain)
        if not feed_info:
            return []
        entries = parse_feed(feed_info["url"])
        if seen_ids is None:
            seen_ids = get_seen_entry_ids(domain)
        new_entries = filter_new_entries(entries, seen_ids)
        unseen = [e for e in entries if e.entry_id not in seen_ids]
        if unseen:
            mark_entries_seen(domain, [
                {"entry_id": e.entry_id, "url": e.url, "title": e.title,
                 "published_date": str(e.published) if e.published else ""}
                for e in unseen
            ])
        return [e.title for e in new_entries[:10] if e.title]
    except Exception as e:
        print(f"[website_diff] feed pre-fetch failed for {domain}: {e}")
        return []


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences by punctuation boundaries, dropping fragments shorter than 20 chars.
    The length filter trims most navigation/footer noise so the diff focuses on real content sentences."""
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 20]


def compute_page_diff(old_content: str, new_content: str) -> dict:
    """Return a structured sentence-level diff: ratio, add/remove counts, and up to 5-sentence previews of each.
    Flags materially_changed when (added+removed)/max(old,new) exceeds 0.15 — tuned to ignore minor copy edits."""
    old_sents = _split_sentences(old_content)
    new_sents = _split_sentences(new_content)
    old_set = set(old_sents)
    new_set = set(new_sents)
    added = [s for s in new_sents if s not in old_set]
    removed = [s for s in old_sents if s not in new_set]
    total = max(len(old_sents), len(new_sents), 1)
    return {
        "materially_changed": (len(added) + len(removed)) / total > 0.15,
        "change_ratio": round((len(added) + len(removed)) / total, 3),
        "sentences_added": len(added),
        "sentences_removed": len(removed),
        "added_preview": added[:5],
        "removed_preview": removed[:5],
    }


def _build_from_prefetch(
    competitor_name: str,
    cluster_id: str,
    new_pages: list[dict],
    schema_added: list[str],
    content_themes: list[str],
) -> WebsiteChanges:
    """Assemble a WebsiteChanges purely from pre-fetch signals without calling Grok, used as the no-HTML-diff shortcut.
    Confidence is 'medium' when new pages exist else 'low'; summary is a short human-readable synopsis."""
    pages_count = len(new_pages)
    schema_str = ", ".join(schema_added[:3]) if schema_added else "none detected"
    themes_str = ", ".join(content_themes[:3]) if content_themes else "none detected"
    summary = (
        f"{pages_count} new page(s) via sitemap. "
        f"Schema: {schema_str}. "
        f"Recent blog topics: {themes_str}."
    )
    return WebsiteChanges(
        competitor_name=competitor_name,
        cluster_id=cluster_id,
        new_pages=new_pages,
        modified_pages=[],
        schema_added=schema_added,
        content_themes=content_themes,
        cluster_relevance="indirect" if (new_pages or schema_added) else "none",
        summary=summary if len(summary) >= 20 else "Structural signals detected via sitemap and feed.",
        confidence="medium" if new_pages else "low",
    )


def _build_user_message(
    trigger,
    diff_context: str,
    new_pages: list[dict],
    schema_added: list[str],
    content_themes: list[str],
) -> str:
    """Render the full user prompt combining diff context, pre-fetched signals, and the exact JSON schema to fill.
    Tells the LLM to treat the pre-fetch lists as authoritative and only add new_pages discovered in the diff itself."""
    prefetch_block = (
        f"\n\nPre-fetched structural signals (use these directly — do not re-derive):\n"
        f"new_pages from sitemap: {json.dumps(new_pages[:10])}\n"
        f"schema_added from HTML: {json.dumps(schema_added)}\n"
        f"recent_blog_titles from feed: {json.dumps(content_themes[:10])}\n"
    )
    return (
        f"Analyse the following website diff for competitor {trigger.competitor_name} "
        f"on cluster '{trigger.cluster_label}' (shift_type: {trigger.shift_type}, "
        f"shift_magnitude: {trigger.shift_magnitude}pp).\n\n"
        f"Diff bundle:\n{diff_context}"
        f"{prefetch_block}\n"
        f"Return a JSON object matching this schema exactly:\n"
        f"{{\n"
        f'  "competitor_name": "{trigger.competitor_name}",\n'
        f'  "cluster_id": "{trigger.cluster_id}",\n'
        f'  "new_pages": [use pre-fetched list; add from diff only if diff reveals additional pages],\n'
        f'  "modified_pages": [page dicts with url and change_summary, found in diff only],\n'
        f'  "schema_added": [use pre-fetched list],\n'
        f'  "content_themes": [combine pre-fetched blog titles with themes from diff],\n'
        f'  "cluster_relevance": "direct|indirect|none",\n'
        f'  "summary": "2-3 sentences",\n'
        f'  "confidence": "high|medium|low|none"\n'
        f"}}"
    )


def _build_diff_context(current: dict, previous: dict | None, page_diff: dict | None = None) -> str:
    """Render the diff context block: both URL/date headers plus either sentence-level diff stats or raw current content.
    Falls back to trimmed current content (2000 chars) when no previous snapshot is available for comparison."""
    cur_text = current.get("content", "")
    cur_header = f"URL: {current.get('url', '')}\nDate: {current.get('scraped_at', '')}\n"
    if not previous:
        return cur_header + "CURRENT CONTENT (no previous snapshot):\n" + cur_text[:2000]
    prev_header = f"URL: {previous.get('url', '')}\nDate: {previous.get('scraped_at', '')}\n"
    if page_diff:
        added = "\n".join(page_diff["added_preview"]) or "none"
        removed = "\n".join(page_diff["removed_preview"]) or "none"
        stats = (
            f"change_ratio: {page_diff['change_ratio']}, "
            f"sentences_added: {page_diff['sentences_added']}, "
            f"sentences_removed: {page_diff['sentences_removed']}"
        )
        return (
            cur_header + prev_header
            + f"DIFF STATS: {stats}\n"
            + f"ADDED SENTENCES:\n{added}\n"
            + f"REMOVED SENTENCES:\n{removed}"
        )
    return cur_header + prev_header + "CURRENT CONTENT:\n" + cur_text[:2000]


def _call_with_fallback(
    system_prompt: str,
    user_message: str,
    competitor_name: str,
    cluster_id: str,
    trigger_key: str,
) -> WebsiteChanges:
    """Invoke Grok with one transient-retry and overwrite competitor/cluster ids before validating.
    Falls back to _fallback() on non-transient error or sustained validation failure."""
    for attempt in range(2):
        try:
            raw = call_extraction_consistent(
                system_prompt=system_prompt,
                user_message=user_message,
                expect_json=True,
                node_name="website_diff_analysis",
                trigger_key=trigger_key,
            )
            raw["competitor_name"] = competitor_name
            raw["cluster_id"] = cluster_id
            validated = _validate_and_repair(raw, competitor_name, cluster_id)
            if validated:
                return validated
            print(f"[website_diff] validation failed for {trigger_key}, using fallback")
            return _fallback(competitor_name, cluster_id)
        except Exception as e:
            if is_transient(e) and attempt == 0:
                continue
            print(f"[website_diff] API error for {trigger_key}: {e}")
            return _fallback(competitor_name, cluster_id)
    return _fallback(competitor_name, cluster_id)


def _validate_and_repair(raw: dict, competitor_name: str, cluster_id: str) -> WebsiteChanges | None:
    """Try strict WebsiteChanges validation; on failure patch the usual missing lists + confidence + summary, retry once.
    Returns None when repair still fails so the caller knows to drop to the no-evidence fallback."""
    try:
        return WebsiteChanges(**raw)
    except Exception:
        pass
    repaired = dict(raw)
    repaired.setdefault("new_pages", [])
    repaired.setdefault("modified_pages", [])
    repaired.setdefault("schema_added", [])
    repaired.setdefault("content_themes", [])
    repaired.setdefault("cluster_relevance", "none")
    repaired.setdefault("summary", "Partial evidence available — some fields missing from Claude output.")
    if repaired.get("confidence") not in {"high", "medium", "low", "none"}:
        repaired["confidence"] = "low"
    if len(repaired.get("summary", "")) < 20:
        repaired["summary"] = "Partial evidence available — summary truncated in Claude output."
    repaired["competitor_name"] = competitor_name
    repaired["cluster_id"] = cluster_id
    try:
        return WebsiteChanges(**repaired)
    except Exception as e:
        print(f"[website_diff] repair failed: {e}")
        return None


def _fallback(competitor_name: str, cluster_id: str) -> WebsiteChanges:
    """Return the canonical 'evidence unavailable' WebsiteChanges stamped with competitor + cluster ids.
    Used on scrape failure, validation failure, or when a previous snapshot is absent and pre-fetches are empty."""
    return WebsiteChanges(
        competitor_name=competitor_name,
        cluster_id=cluster_id,
        **_FALLBACK_WEBSITE_CHANGES,
    )
