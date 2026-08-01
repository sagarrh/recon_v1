# cache.py — Operational cache shim for the Scout pipeline (Supabase-backed).
# Purpose: Single entry point for snapshot/feed/sitemap/AI-response cache I/O against Supabase.
# Scope: Stateless dispatchers only; underlying writes go to Supabase via scout/db/supabase_client.py.
# Consumers: scout/nodes/website_diff.py, scout/nodes/blog_monitoring.py, scout/nodes/ai_response_analysis.py, scout/integrations/feed_parser.py, scripts/collect_feeds.py.
import logging
from datetime import date, timedelta

from scout.utils import now_iso as _now_iso
from scout.utils import sb as _sb

log = logging.getLogger(__name__)


def _today_iso() -> str:
    """Return today's date as YYYY-MM-DD; used for first_seen_at stamps on cache inserts.
    Kept as a helper so every date filter compares against the same format."""
    return str(date.today())


# ---------- website snapshots ----------

def get_prev_snapshot(domain: str) -> dict | None:
    """Return the most recent cached page dict for a domain, or None when no snapshot exists.
    Reads from Supabase website_snapshots.pages (jsonb), newest week_date first."""
    try:
        resp = (
            _sb().table("website_snapshots")
            .select("pages,week_date")
            .eq("domain", domain)
            .order("week_date", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        return resp.data[0].get("pages") or None
    except Exception as e:
        log.warning("[cache] get_prev_snapshot(%s) failed: %s", domain, e)
        return None


def store_snapshot(
    domain: str,
    week_date: str,
    pages: dict,
    crawl_metadata: dict | None = None,
    run_id: str | None = None,
) -> None:
    """Persist a website snapshot for (domain, week_date) by upserting into Supabase website_snapshots.
    on_conflict matches the table's (domain, week_date) unique constraint so a same-week re-run UPDATEs the row (refreshing run_id/pages/crawl_metadata) instead of colliding (23505)."""
    try:
        row = {
            "run_id": run_id,
            "domain": domain,
            "week_date": week_date,
            "pages": pages or {},
            "page_count": len(pages or {}),
            "crawl_metadata": crawl_metadata or {},
        }
        _sb().table("website_snapshots").upsert(
            row, on_conflict="domain,week_date"
        ).execute()
    except Exception as e:
        log.warning("[cache] store_snapshot(%s) failed: %s", domain, e)


# ---------- competitor feeds (discovery cache) ----------

def get_cached_feed(domain: str) -> dict | None:
    """Return the cached feed-discovery row for a domain, or None when never discovered.
    Reads from Supabase competitor_feeds and normalizes failure_count/last_success_at into the legacy dict shape."""
    try:
        resp = (
            _sb().table("competitor_feeds")
            .select("feed_url,feed_type,failure_count,last_success_at")
            .eq("domain", domain)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        r = resp.data[0]
        return {
            "feed_url": r.get("feed_url"),
            "feed_type": r.get("feed_type"),
            "consecutive_failures": int(r.get("failure_count") or 0),
            "last_checked_at": r.get("last_success_at"),
        }
    except Exception as e:
        log.warning("[cache] get_cached_feed(%s) failed: %s", domain, e)
        return None


def cache_feed(domain: str, feed_url: str | None, feed_type: str | None) -> None:
    """Upsert a discovered feed URL for a domain into Supabase competitor_feeds, marking is_active and zeroing failure_count.
    No-op when feed_url is None. UNIQUE(domain) drives the upsert conflict target."""
    if feed_url is None:
        return
    try:
        row = {
            "domain": domain,
            "feed_url": feed_url,
            "feed_type": feed_type,
            "discovery_method": "auto",
            "is_active": True,
            "failure_count": 0,
            "last_success_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        _sb().table("competitor_feeds").upsert(row, on_conflict="domain").execute()
    except Exception as e:
        log.warning("[cache] cache_feed(%s) failed: %s", domain, e)


def increment_feed_failures(domain: str) -> None:
    """Bump the consecutive failure count for a cached feed row and stamp last_failure_at.
    Reads-then-writes because Supabase has no atomic increment."""
    try:
        resp = (
            _sb().table("competitor_feeds")
            .select("failure_count")
            .eq("domain", domain)
            .limit(1)
            .execute()
        )
        current = int(resp.data[0].get("failure_count") or 0) if resp.data else 0
        (
            _sb().table("competitor_feeds")
            .update({
                "failure_count": current + 1,
                "last_failure_at": _now_iso(),
                "updated_at": _now_iso(),
            })
            .eq("domain", domain)
            .execute()
        )
    except Exception as e:
        log.warning("[cache] increment_feed_failures(%s) failed: %s", domain, e)


def clear_feed_cache(domain: str) -> None:
    """Delete the cached feed-discovery row for a domain from Supabase, forcing fresh discovery next run.
    Swallows errors so a cache miss never aborts collection."""
    try:
        _sb().table("competitor_feeds").delete().eq("domain", domain).execute()
    except Exception as e:
        log.warning("[cache] clear_feed_cache(%s) failed: %s", domain, e)


# ---------- feed entries seen ----------

def get_seen_entry_ids(domain: str) -> set[str]:
    """Return the set of feed entry_ids already cached for a domain — used to filter 'new' entries.
    Empty set on first scan; reads from Supabase feed_entries_seen."""
    try:
        resp = (
            _sb().table("feed_entries_seen")
            .select("entry_id")
            .eq("domain", domain)
            .execute()
        )
        return {r["entry_id"] for r in (resp.data or []) if r.get("entry_id")}
    except Exception as e:
        log.warning("[cache] get_seen_entry_ids(%s) failed: %s", domain, e)
        return set()


def mark_entries_seen(domain: str, entries: list[dict]) -> None:
    """Persist newly observed feed entries for a domain via Supabase upsert; idempotent on UNIQUE(domain, entry_id).
    Entries lacking entry_id are skipped; an empty list is a no-op."""
    if not entries:
        return
    try:
        today = _today_iso()
        rows = [
            {
                "domain": domain,
                "entry_id": e["entry_id"],
                "entry_url": e.get("url", ""),
                "first_seen_at": today,
            }
            for e in entries
            if e.get("entry_id")
        ]
        if not rows:
            return
        _sb().table("feed_entries_seen").upsert(rows, on_conflict="domain,entry_id").execute()
    except Exception as e:
        log.warning("[cache] mark_entries_seen(%s) failed: %s", domain, e)


def get_new_feed_entries_since(domain: str, since_date: str) -> list[dict]:
    """Return feed entries first seen at or after since_date for a domain; used for blog-detection windowing.
    Reads from Supabase feed_entries_seen (title/published_date are not stored, returned empty)."""
    try:
        resp = (
            _sb().table("feed_entries_seen")
            .select("entry_id,entry_url,first_seen_at")
            .eq("domain", domain)
            .gte("first_seen_at", since_date)
            .execute()
        )
        return [
            {
                "entry_id": r.get("entry_id"),
                "url": r.get("entry_url"),
                "title": "",
                "published_date": "",
                "first_seen_at": r.get("first_seen_at"),
            }
            for r in (resp.data or [])
        ]
    except Exception as e:
        log.warning("[cache] get_new_feed_entries_since(%s) failed: %s", domain, e)
        return []


# ---------- sitemap URLs known ----------

def get_known_sitemap_urls(domain: str) -> set[str]:
    """Return the set of sitemap URLs already cached for a domain.
    Reads from Supabase sitemap_urls_known."""
    try:
        resp = (
            _sb().table("sitemap_urls_known")
            .select("url")
            .eq("domain", domain)
            .execute()
        )
        return {r["url"] for r in (resp.data or []) if r.get("url")}
    except Exception as e:
        log.warning("[cache] get_known_sitemap_urls(%s) failed: %s", domain, e)
        return set()


def store_sitemap_urls(domain: str, urls: list[dict]) -> None:
    """Persist sitemap URLs for a domain via Supabase upsert; idempotent on UNIQUE(domain, url).
    lastmod/is_blog_url are not stored; an empty list is a no-op."""
    if not urls:
        return
    try:
        today = _today_iso()
        rows = [
            {"domain": domain, "url": u["url"], "first_seen_at": today}
            for u in urls
            if u.get("url")
        ]
        if not rows:
            return
        _sb().table("sitemap_urls_known").upsert(rows, on_conflict="domain,url").execute()
    except Exception as e:
        log.warning("[cache] store_sitemap_urls(%s) failed: %s", domain, e)


def get_new_sitemap_urls_since(domain: str, since_date: str) -> list[dict]:
    """Return sitemap URLs first seen at or after since_date for a domain.
    Reads from Supabase sitemap_urls_known; is_blog_url is not tracked, so downstream classification filters."""
    try:
        resp = (
            _sb().table("sitemap_urls_known")
            .select("url,first_seen_at")
            .eq("domain", domain)
            .gte("first_seen_at", since_date)
            .execute()
        )
        return [
            {"url": r.get("url"), "lastmod": "", "first_seen_at": r.get("first_seen_at")}
            for r in (resp.data or [])
        ]
    except Exception as e:
        log.warning("[cache] get_new_sitemap_urls_since(%s) failed: %s", domain, e)
        return []


# ---------- AI response bundle ----------

def get_ai_response_bundle(cluster_id: str, sync_date: str | None = None, weeks: int = 3) -> dict:
    """Return {current_responses, baseline_responses} for a cluster: latest sync as current, latest sync >=4 days earlier as baseline.
    Pulls from Supabase ai_responses within a `weeks`-wide window (default 3 = the original 21 days); caps each side to one row (R3-2 widens the window)."""
    try:
        current = sync_date or _today_iso()
        try:
            current_d = date.fromisoformat(current)
        except ValueError:
            return {}
        window_start = str(current_d - timedelta(days=weeks * 7))
        resp = (
            _sb().table("ai_responses")
            .select("platform,query,answers_list,synced_at")
            .eq("cluster_id", cluster_id)
            .gte("synced_at", window_start)
            .order("synced_at", desc=True)
            .limit(50)
            .execute()
        )
        rows = resp.data or []
        current_row = None
        baseline_row = None
        current_d_picked: date | None = None
        for r in rows:
            synced_at = r.get("synced_at") or ""
            try:
                row_d = date.fromisoformat(synced_at[:10])
            except ValueError:
                continue
            if current_row is None:
                current_row = r
                current_d_picked = row_d
                continue
            if baseline_row is None and current_d_picked is not None and (current_d_picked - row_d).days >= 4:
                baseline_row = r
                break

        def _to_entry(r: dict) -> dict:
            answer = r.get("answers_list")
            if isinstance(answer, (list, dict)):
                import json as _json
                answer_text = _json.dumps(answer, ensure_ascii=False)
            else:
                answer_text = answer or ""
            return {
                "platform": r.get("platform") or "",
                "query": r.get("query") or "",
                "response": answer_text,
            }

        bundle = {"current_responses": [], "baseline_responses": []}
        if current_row is not None:
            bundle["current_responses"].append(_to_entry(current_row))
        if baseline_row is not None:
            bundle["baseline_responses"].append(_to_entry(baseline_row))
        if not bundle["current_responses"] and not bundle["baseline_responses"]:
            log.warning(
                "[cache] get_ai_response_bundle returned empty for cluster_id=%r (type=%s, sync_date=%s)",
                cluster_id, type(cluster_id).__name__, current,
            )
            return {}
        return bundle
    except Exception as e:
        log.warning("[cache] get_ai_response_bundle(%s) failed: %s", cluster_id, e)
        return {}


# ---------- GEO report_data AI baseline (read-only fallback) ----------

def get_geo_ai_baseline_bundle(client_id: str, cluster_id: str) -> dict:
    """Return a report_data-derived {current_responses, baseline_responses} bundle for a cluster, or {} when unavailable.
    Gated by geo_report_data_sov_enabled; delegates to report_data_sov so ai_responses-less clusters still get a baseline."""
    from scout.config import get_config
    if not get_config().geo_report_data_sov_enabled:
        return {}
    try:
        from scout.db.report_data_sov import get_geo_ai_baseline_bundle as _impl
        return _impl(_sb(), client_id, cluster_id) or {}
    except Exception as e:
        log.warning("[cache] get_geo_ai_baseline_bundle(%s,%s) failed: %s", client_id, cluster_id, e)
        return {}


# ---------- GEO scraped-content / website-files / schema (read-only) ----------

def get_geo_scraped_page(domain: str) -> dict | None:
    """Return a fresh GEO company_scraped_data_cache page {url, content, scraped_at} for a domain, or None.
    Gated by geo_scraped_cache_enabled; TTL from geo_scraped_cache_ttl_days. Lets website_diff skip Bright Data."""
    from scout.config import get_config
    cfg = get_config()
    if not cfg.geo_scraped_cache_enabled:
        return None
    try:
        from scout.db.geo_cache import get_scraped_page
        return get_scraped_page(domain, cfg.geo_scraped_cache_ttl_days)
    except Exception as e:
        log.warning("[cache] get_geo_scraped_page(%s) failed: %s", domain, e)
        return None


def get_geo_website_files(client_id: str) -> dict | None:
    """Return the client's latest report_data.website_files_data (robots/ai_bots/llms), or None. CLIENT-scoped.
    Gated by geo_client_readiness_enabled; intended for client-site readiness analysis, not competitor diffs."""
    from scout.config import get_config
    if not get_config().geo_client_readiness_enabled:
        return None
    try:
        from scout.db.geo_cache import get_website_files
        return get_website_files(client_id)
    except Exception as e:
        log.warning("[cache] get_geo_website_files(%s) failed: %s", client_id, e)
        return None


def get_geo_schema_types(client_id: str) -> list[str] | None:
    """Return JSON-LD @types from the client's latest report_data.schema_data, or None. CLIENT-scoped.
    Gated by geo_client_readiness_enabled; intended for client-site readiness analysis, not competitor diffs."""
    from scout.config import get_config
    if not get_config().geo_client_readiness_enabled:
        return None
    try:
        from scout.db.geo_cache import get_schema_types
        return get_schema_types(client_id)
    except Exception as e:
        log.warning("[cache] get_geo_schema_types(%s) failed: %s", client_id, e)
        return None
