# sitemap_parser.py — Sitemap discovery, parsing, and blog-URL filtering.
# Purpose: Fetches a domain's sitemap.xml (or robots.txt-declared sitemap), follows sitemap-index trees, extracts URLs.
# Scope: Heuristics for blog-URL classification, include/exclude regex filters, lxml XML parsing, bounded crawl.
# Consumers: scout/nodes/blog_monitoring.py uses this as the sitemap fallback when feed discovery fails.
import re

import requests
from lxml import etree

from scout.config import get_config

_BLOG_INCLUDE_SEGMENTS = {
    "blog", "insights", "resources", "news", "articles", "posts",
    "thought-leadership", "updates",
}

_BLOG_DATE_PATTERN = re.compile(r"/\d{4}/\d{2}/", re.IGNORECASE)

_BLOG_EXCLUDE_PATTERNS = re.compile(
    r"/tag/|/category/|/page/\d|/author/|/wp-json/|/feed/|"
    r"/wp-content/|/wp-admin/|/cdn-cgi/|\.xml$|\.pdf$",
    re.IGNORECASE,
)

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

_HEADERS = {"User-Agent": "ScoutBot/1.0 (sitemap reader)"}

# Untrusted remote XML: cap the body (memory) and disable entity resolution + network access
# (defeats billion-laughs entity expansion and external-entity XXE/SSRF). Built once, reused.
MAX_XML_BYTES = 10 * 1024 * 1024
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def fetch_sitemap(domain: str) -> list[dict]:
    """Return a bounded list of {url, lastmod} dicts harvested from a domain's sitemap (or its sitemap-index tree).
    Respects sitemap_max_urls and sitemap_max_child_sitemaps config; returns [] when no sitemap can be discovered."""
    config = get_config()
    base = f"https://{domain}" if not domain.startswith("http") else domain
    max_urls = config.sitemap_max_urls

    sitemap_url = _discover_sitemap_url(base)
    if not sitemap_url:
        return []

    xml = _fetch_xml(sitemap_url)
    if not xml:
        return []

    if _is_sitemap_index(xml):
        return _process_sitemap_index(xml, config.sitemap_max_child_sitemaps, max_urls)

    return _parse_urlset(xml, max_urls)


def _is_blog_url(url: str) -> bool:
    """Return True when a URL's path segments include a blog-ish keyword OR it matches the /YYYY/MM/ date pattern.
    Used to prioritize blog-like child sitemaps and to flag individual URLs for blog_monitoring."""
    segments = set(url.rstrip("/").split("/"))
    return bool(segments & _BLOG_INCLUDE_SEGMENTS) or bool(_BLOG_DATE_PATTERN.search(url))


def filter_blog_urls(urls: list[dict], domain: str = "") -> list[dict]:
    """Keep only blog-like URLs from a sitemap harvest, stamping is_blog_url=1 on each kept entry.
    Excludes tag/category/paginated archives via a precompiled blacklist regex before include checks."""
    results = []
    for entry in urls:
        url = entry.get("url", "")
        if _BLOG_EXCLUDE_PATTERNS.search(url):
            continue
        if _is_blog_url(url):
            entry["is_blog_url"] = 1
            results.append(entry)
    return results


def detect_new_sitemap_urls(current_urls: list[str], known_urls: set[str]) -> list[str]:
    """Return the subset of current_urls not yet present in known_urls, preserving input order.
    Caller typically feeds sitemap results + local cache (get_known_sitemap_urls) to diff week-over-week."""
    return [u for u in current_urls if u not in known_urls]


def _discover_sitemap_url(base: str) -> str | None:
    """Try /sitemap.xml first, then scan /robots.txt for a Sitemap: line; return the discovered URL or None.
    Swallows RequestExceptions so a flaky server doesn't bubble up past blog_monitoring's orchestration."""
    sitemap_url = f"{base}/sitemap.xml"
    try:
        resp = requests.get(sitemap_url, timeout=30, headers=_HEADERS, allow_redirects=True)
        if resp.status_code == 200 and resp.text.strip():
            return sitemap_url
    except requests.RequestException:
        pass

    try:
        robots_resp = requests.get(f"{base}/robots.txt", timeout=15, headers=_HEADERS)
        if robots_resp.status_code == 200:
            for line in robots_resp.text.splitlines():
                if line.strip().lower().startswith("sitemap:"):
                    return line.split(":", 1)[1].strip()
    except requests.RequestException:
        pass

    return None


def _fetch_xml(url: str) -> bytes | None:
    """GET up to MAX_XML_BYTES of a URL as raw bytes, or None on HTTP/network error or over-cap (with a log line).
    Streams so an oversized (multi-GB) body is rejected before being fully read into memory; keeps bytes so lxml honors declared encoding."""
    try:
        with requests.get(url, timeout=30, headers=_HEADERS, allow_redirects=True, stream=True) as resp:
            resp.raise_for_status()
            chunks, total = [], 0
            for chunk in resp.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_XML_BYTES:
                    print(f"[sitemap_parser] {url} exceeded {MAX_XML_BYTES} bytes — skipping")
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as e:
        print(f"[sitemap_parser] Failed to fetch {url}: {e}")
        return None


def _is_sitemap_index(xml: bytes) -> bool:
    """Cheap peek: True when <sitemapindex appears in the first 2000 bytes, meaning we must recurse into children.
    Avoids the cost of a full XML parse when the caller only needs to decide urlset vs sitemapindex."""
    return b"<sitemapindex" in xml[:2000]


def _process_sitemap_index(xml: bytes, max_children: int, max_urls: int) -> list[dict]:
    """Walk a sitemap-index, prioritizing blog-like child sitemaps, and flatten up to max_urls across the selection.
    Returns [] on XML parse error; accumulates urls across children until the global cap is hit, then stops."""
    try:
        root = etree.fromstring(xml, parser=_SAFE_PARSER)
    except etree.XMLSyntaxError as e:
        print(f"[sitemap_parser] XML parse error in sitemap index: {e}")
        return []

    child_urls = []
    for sitemap in root.findall("sm:sitemap", _SITEMAP_NS):
        loc = sitemap.find("sm:loc", _SITEMAP_NS)
        if loc is not None and loc.text:
            child_urls.append(loc.text.strip())

    blog_children = [u for u in child_urls if _is_blog_url(u)]
    other_children = [u for u in child_urls if u not in blog_children]
    prioritized = blog_children + other_children
    selected = prioritized[:max_children]

    all_urls = []
    for child_url in selected:
        child_xml = _fetch_xml(child_url)
        if child_xml:
            urls = _parse_urlset(child_xml, max_urls - len(all_urls))
            all_urls.extend(urls)
            if len(all_urls) >= max_urls:
                break

    return all_urls[:max_urls]


def _parse_urlset(xml: bytes, max_urls: int) -> list[dict]:
    """Parse a single sitemap urlset XML and return up to max_urls entries as {url, lastmod} dicts.
    Returns [] on XML parse failure; lastmod is None when absent from the sitemap entry."""
    try:
        root = etree.fromstring(xml, parser=_SAFE_PARSER)
    except etree.XMLSyntaxError as e:
        print(f"[sitemap_parser] XML parse error: {e}")
        return []

    urls = []
    for url_elem in root.findall("sm:url", _SITEMAP_NS):
        if len(urls) >= max_urls:
            break
        loc = url_elem.find("sm:loc", _SITEMAP_NS)
        lastmod = url_elem.find("sm:lastmod", _SITEMAP_NS)
        if loc is not None and loc.text:
            urls.append({
                "url": loc.text.strip(),
                "lastmod": lastmod.text.strip() if lastmod is not None and lastmod.text else None,
            })

    return urls
