# feed_parser.py — RSS/Atom feed discovery and parsing for competitor blog monitoring.
# Purpose: Discovers feed URLs on competitor domains, parses entries, and filters to 'new since last scan'.
# Scope: Common-path probing, HTML <link rel="alternate"> extraction, feedparser-driven entry building, 14-day first-scan window.
# Consumers: scout/nodes/blog_monitoring.py — this module is the feed half of the blog detection pipeline.
import hashlib
import re
from datetime import UTC, date, datetime, timedelta

import feedparser
import requests

from scout.config import get_config
from scout.db.cache import cache_feed, get_cached_feed
from scout.models.blog import FeedEntry

_COMMON_FEED_PATHS = [
    "/feed/",
    "/rss.xml",
    "/atom.xml",
    "/feed.xml",
    "/blog/feed/",
    "/blog/rss.xml",
    "/blog/atom.xml",
    "/index.xml",
]

_FEED_CONTENT_TYPES = {"application/rss+xml", "application/atom+xml", "text/xml", "application/xml"}


_FEED_RETRY_DAYS = 7


def discover_feed_url(domain: str) -> dict | None:
    """Discover a competitor's RSS/Atom feed URL, honoring the Supabase cache and a 7-day no-feed re-probe window.
    Returns {'url', 'type'} or None; writes the discovery result back to competitor_feeds via the cache shim so subsequent runs short-circuit."""
    cached = get_cached_feed(domain)
    if cached is not None:
        if cached["feed_url"]:
            return {"url": cached["feed_url"], "type": cached["feed_type"]}
        last_checked = cached.get("last_checked_at") or ""
        try:
            age_days = (date.today() - date.fromisoformat(last_checked[:10])).days
        except (ValueError, TypeError):
            age_days = _FEED_RETRY_DAYS
        if age_days < _FEED_RETRY_DAYS:
            return None

    config = get_config()
    timeout = config.feed_discovery_timeout_seconds
    base = f"https://{domain}" if not domain.startswith("http") else domain

    for path in _COMMON_FEED_PATHS:
        url = f"{base}{path}"
        try:
            resp = requests.get(url, timeout=timeout, allow_redirects=True,
                                headers={"User-Agent": "ScoutBot/1.0 (feed reader)"})
            if resp.status_code == 200 and _looks_like_feed(resp.text, resp.headers.get("content-type", "")):
                feed_type = "atom" if "atom" in resp.text[:500].lower() else "rss"
                cache_feed(domain, url, feed_type)
                return {"url": url, "type": feed_type}
        except requests.RequestException:
            continue

    for page_path in ["", "/blog/", "/blog"]:
        feed_from_html = _extract_feed_link_from_html(base + page_path, timeout)
        if feed_from_html:
            cache_feed(domain, feed_from_html["url"], feed_from_html["type"])
            return feed_from_html

    cache_feed(domain, None, None)
    return None


def parse_feed(feed_url: str) -> list[FeedEntry]:
    """Fetch and parse a feed URL into up to 50 FeedEntry models with id, url, title, date, author, summary.
    On network/HTTP error returns an empty list; HTML in summaries is stripped and clipped to 500 chars."""
    try:
        resp = requests.get(
            feed_url, timeout=30,
            headers={"User-Agent": "ScoutBot/1.0 (feed reader)"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[feed_parser] Failed to fetch feed {feed_url}: {e}")
        return []

    parsed = feedparser.parse(resp.text)
    entries = []

    for entry in parsed.entries[:50]:
        entry_id = _get_entry_id(entry)
        url = getattr(entry, "link", "") or ""
        title = getattr(entry, "title", "") or ""
        published = _parse_date(entry)
        author = getattr(entry, "author", None)
        summary = getattr(entry, "summary", None)
        if summary:
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = " ".join(summary.split())[:500]

        entries.append(FeedEntry(
            entry_id=entry_id,
            url=url,
            title=title,
            published=published,
            author=author,
            summary=summary,
            content_snippet=summary[:500] if summary else None,
        ))

    return entries


def filter_new_entries(
    entries: list[FeedEntry],
    seen_ids: set[str],
    first_scan_lookback_days: int = 14,
) -> list[FeedEntry]:
    """Return entries not present in seen_ids, further capped to the last N days on first scans to avoid backfill floods.
    On first scan (seen_ids empty), entries with no publish date fall through; otherwise pure id-based diff."""
    is_first_scan = len(seen_ids) == 0
    new_entries = [e for e in entries if e.entry_id not in seen_ids]

    if is_first_scan and new_entries:
        cutoff = datetime.now(UTC) - timedelta(days=first_scan_lookback_days)
        filtered = []
        for e in new_entries:
            if (e.published and e.published.replace(tzinfo=UTC if e.published.tzinfo is None else e.published.tzinfo) >= cutoff) or not e.published:
                filtered.append(e)
        return filtered

    return new_entries


def _looks_like_feed(text: str, content_type: str) -> bool:
    """Heuristic: True when content-type matches a known feed MIME or the body contains <rss/<feed/<atom.
    Only inspects the first 1000 chars of the body so large responses don't pay a scan cost."""
    ct_lower = content_type.lower()
    if any(ft in ct_lower for ft in _FEED_CONTENT_TYPES):
        return True
    snippet = text[:1000].lower()
    return "<rss" in snippet or "<feed" in snippet or "<atom" in snippet


def _extract_feed_link_from_html(url: str, timeout: int) -> dict | None:
    """Pull a <link rel='alternate'> feed URL (RSS or Atom) out of an HTML page, rewriting relative to absolute.
    Returns None on fetch failure or when no feed link is present; only the first 50KB of HTML is scanned."""
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": "ScoutBot/1.0 (feed reader)"})
        if resp.status_code != 200:
            return None
    except requests.RequestException:
        return None

    html = resp.text[:50000]
    patterns = [
        (r'<link[^>]+type="application/rss\+xml"[^>]+href="([^"]+)"', "rss"),
        (r'<link[^>]+href="([^"]+)"[^>]+type="application/rss\+xml"', "rss"),
        (r'<link[^>]+type="application/atom\+xml"[^>]+href="([^"]+)"', "atom"),
        (r'<link[^>]+href="([^"]+)"[^>]+type="application/atom\+xml"', "atom"),
    ]
    for pattern, feed_type in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            feed_url = match.group(1)
            if not feed_url.startswith("http"):
                feed_url = url.rstrip("/") + "/" + feed_url.lstrip("/")
            return {"url": feed_url, "type": feed_type}
    return None


def _get_entry_id(entry) -> str:
    """Return a stable identifier for a feed entry (guid/id when present, else sha256(url|title) truncated to 16 hex).
    Used as the dedup key in feed_entries_seen so reruns don't re-ingest the same post."""
    guid = getattr(entry, "id", None) or getattr(entry, "guid", None)
    if guid:
        return guid
    url = getattr(entry, "link", "") or ""
    title = getattr(entry, "title", "") or ""
    return hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:16]


def _parse_date(entry) -> datetime | None:
    """Coerce feedparser's published_parsed/updated_parsed struct_time into a UTC-aware datetime, else None.
    Tolerates malformed timestamps (ValueError/OverflowError/OSError) by trying the next candidate."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                from time import mktime
                return datetime.fromtimestamp(mktime(parsed), tz=UTC)
            except (ValueError, OverflowError, OSError):
                continue
    return None
