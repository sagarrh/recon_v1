# bright_data.py — Bright Data Web Unlocker + SERP API client for Scout.
# Purpose: Wraps scrape_website() and serp_search() used by the investigation nodes to gather competitor evidence.
# Scope: HTTP calls to api.brightdata.com with retries, HTML text extraction, and shift-type-aware SERP query templates.
# Consumers: scout/nodes/web_intelligence.py (SERP), scout/nodes/website_diff.py (scrape); configured via ScoutConfig.
import contextlib
import time
import urllib.parse
from datetime import date

import requests

from scout.config import get_config

_SERP_QUERY_TEMPLATES = {
    "gain": [
        ("reviews", "{competitor} review {year}"),
        ("press",   "{competitor} press release announcement"),
        ("press",   "{competitor} {cluster} new features partnership"),
        ("social",  "{competitor} LinkedIn company news"),
        ("awards",  "{competitor} award certification recognition"),
        ("social",  "{competitor} case study customer success"),
    ],
    "displacement": [
        ("reviews", "{competitor} review {year}"),
        ("press",   "{competitor} {cluster} competitive advantage"),
        ("press",   "{competitor} product launch announcement"),
        ("press",   "{competitor} pricing changes"),
        ("press",   "{competitor} press coverage news"),
    ],
    "new_entrant": [
        ("press",  "{competitor} company about founded"),
        ("press",  "{competitor} launch announcement funding"),
        ("press",  "{competitor} {cluster} positioning"),
        ("social", "{competitor} LinkedIn profile news"),
    ],
    "loss": [
        ("reviews", "{competitor} negative reviews complaints"),
        ("press",   "{competitor} outage issues controversy"),
        ("press",   "{competitor} pricing increase changes"),
        ("press",   "{competitor} {cluster} problems"),
    ],
    "blog_detected": [
        ("press",   "{competitor} blog post announcement {cluster}"),
        ("press",   "{competitor} content marketing strategy"),
        ("press",   "{competitor} thought leadership {cluster}"),
        ("social",  "{competitor} blog launch LinkedIn"),
        ("reviews", "{competitor} {cluster} comparison guide"),
    ],
}

# R3-2 — broader query set appended to the base shift-type set when a source trips its evidence floor.
_SERP_ENRICH_TEMPLATES = [
    ("reviews", "{competitor} {cluster} alternatives"),
    ("press",   "{competitor} interview podcast"),
    ("social",  "{competitor} hiring jobs"),
    ("press",   "{competitor} {cluster} {year} vs {prev_year}"),
]


def _full_url(url: str) -> str:
    return url if url.startswith("http") else f"https://{url}"


def _request_web_unlocker(url: str) -> str:
    """POST a URL to the Bright Data Web Unlocker and return the raw response text (HTML/text).
    Retries transient timeout/connection/HTTP errors with exponential backoff; raises on sustained failure.
    Shared by scrape_website (extracted text), scrape_raw (HTML), and the review/logo scrapers."""
    config = get_config()
    last_err: Exception | None = None
    for attempt in range(config.bright_data_scrape_max_attempts):
        try:
            resp = requests.post(
                "https://api.brightdata.com/request",
                headers={
                    "Authorization": f"Bearer {config.bright_data_api_key}",
                    "Content-Type": "application/json",
                },
                json={"zone": config.bright_data_web_unlocker_zone, "url": url, "format": "raw"},
                timeout=config.bright_data_scrape_timeout_seconds,
            )
            resp.raise_for_status()
            return resp.text
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            last_err = e
            if attempt < config.bright_data_scrape_max_attempts - 1:
                print(f"[bright_data] web-unlocker transient error for {url}: {e} — retrying")
                time.sleep(2 ** attempt)
                continue
            raise
    raise last_err if last_err else RuntimeError(f"web-unlocker failed for {url}")


def scrape_website(domain: str) -> dict:
    """Fetch a page via Bright Data Web Unlocker, extract readable text, return {url, content, scraped_at}.
    content is trimmed to 8000 chars to bound downstream prompt size."""
    url = _full_url(domain)
    return {"url": url, "content": _extract_text(_request_web_unlocker(url))[:8000], "scraped_at": str(date.today())}


def scrape_raw(url: str) -> dict:
    """Return {url, html} with the RAW page HTML (for link / og:image extraction the text-extractor would strip)."""
    full = _full_url(url)
    return {"url": full, "html": _request_web_unlocker(full)}


def scrape_pages(urls: list[str], max_pages: int = 6) -> list[dict]:
    """Polite, deduped, capped multi-page Web Unlocker scrape. Returns [{url, content}]; failures are skipped."""
    out: list[dict] = []
    seen: set[str] = set()
    for u in urls:
        if len(out) >= max_pages:
            break
        key = (u or "").strip().lower().rstrip("/")
        if not u or key in seen:
            continue
        seen.add(key)
        try:
            page = scrape_website(u)
            if page.get("content"):
                out.append({"url": page.get("url") or u, "content": page["content"]})
        except Exception as e:
            print(f"[bright_data] scrape_pages skip {u}: {e}")
        time.sleep(0.3)
    return out


def extract_logo(domain: str) -> str | None:
    """Scrape the homepage raw HTML and return an og:image / icon URL, or None (never fabricated)."""
    try:
        html = scrape_raw(domain).get("html", "")
    except Exception as e:
        print(f"[bright_data] extract_logo failed for {domain}: {e}")
        return None
    return _extract_og_image(html)


def _extract_og_image(html: str) -> str | None:
    import re
    for pat in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'):
        m = re.search(pat, html or "", re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def serp_search(competitor_name: str, cluster_label: str, shift_type: str) -> dict:
    """Run the shift-type-appropriate SERP query set for a competitor and return {queries_run, serp_results}.
    Sleeps 0.5s between queries to be polite to the SERP endpoint; each result carries query/category/snippets."""
    query_dicts = _build_serp_queries(competitor_name, cluster_label, shift_type)
    results = []
    for i, qd in enumerate(query_dicts):
        if i > 0:
            time.sleep(0.5)
        snippets = _call_serp_api(qd["query"])
        results.append({"query": qd["query"], "category": qd["category"], "snippets": snippets})
    return {"queries_run": query_dicts, "serp_results": results}


def _build_serp_queries(competitor_name: str, cluster_label: str, shift_type: str) -> list[dict]:
    """Expand the query template set for shift_type into concrete [{query, category}] entries.
    Falls back to the 'gain' template when shift_type is unknown; year is substituted from date.today()."""
    templates = _SERP_QUERY_TEMPLATES.get(shift_type, _SERP_QUERY_TEMPLATES["gain"])
    year = date.today().year
    return [
        {"query": t.format(competitor=competitor_name, cluster=cluster_label, year=year), "category": cat}
        for cat, t in templates
    ]


def _build_enrich_queries(competitor_name: str, cluster_label: str) -> list[dict]:
    """Expand the broader R3-2 enrichment template set into concrete [{query, category}] entries.
    Used only when web_intelligence trips its evidence floor, to widen SERP coverage beyond the base set."""
    year = date.today().year
    return [
        {"query": t.format(competitor=competitor_name, cluster=cluster_label, year=year, prev_year=year - 1), "category": cat}
        for cat, t in _SERP_ENRICH_TEMPLATES
    ]


def serp_search_enriched(competitor_name: str, cluster_label: str, shift_type: str) -> dict:
    """Run the base shift-type SERP set PLUS the broader enrichment set and return the merged {queries_run, serp_results}.
    Invoked by enrichment.enrich_web_intel when web_intelligence trips its evidence floor (R3-2); cost is not a constraint."""
    query_dicts = _build_serp_queries(competitor_name, cluster_label, shift_type) + _build_enrich_queries(competitor_name, cluster_label)
    results = []
    for i, qd in enumerate(query_dicts):
        if i > 0:
            time.sleep(0.5)
        snippets = _call_serp_api(qd["query"])
        results.append({"query": qd["query"], "category": qd["category"], "snippets": snippets})
    return {"queries_run": query_dicts, "serp_results": results}


def _call_serp_api(query: str) -> list[dict]:
    """Call Bright Data SERP for a single query and return a list of {title, snippet, url} organic results.
    Retries once on timeout/connection/HTTP errors with exponential backoff; returns [] on sustained
    failure or an unparseable body so a partial SERP pull still yields usable evidence."""
    config = get_config()
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num=10&hl=en"
    for attempt in range(config.bright_data_serp_max_attempts):
        try:
            resp = requests.post(
                "https://api.brightdata.com/request",
                headers={
                    "Authorization": f"Bearer {config.bright_data_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "zone": config.bright_data_serp_zone,
                    "url": search_url,
                    "format": "raw",
                    "data_format": "parsed_light",
                },
                timeout=config.bright_data_serp_timeout_seconds,
            )
            resp.raise_for_status()
            return _parse_serp_results(resp.json())
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            if attempt < config.bright_data_serp_max_attempts - 1:
                print(f"[bright_data] SERP transient error for '{query}': {e} — retrying")
                time.sleep(2 ** attempt)
                continue
            print(f"[bright_data] SERP query failed for '{query}' after {config.bright_data_serp_max_attempts} attempts: {e}")
            return []
        except requests.exceptions.JSONDecodeError:
            ct = resp.headers.get("Content-Type", "")
            print(f"[bright_data] SERP returned non-JSON body for '{query}' (content-type '{ct}'): {resp.text[:200]!r}")
            return []
        except Exception as e:
            print(f"[bright_data] SERP query failed for '{query}': {e}")
            return []
    return []


def _parse_serp_results(data: dict) -> list[dict]:
    """Project the SERP API JSON response's 'organic' array into the minimal {title, snippet, url} shape we use.
    Entries missing both title and description are dropped to keep downstream evidence dense."""
    organic = data.get("organic", [])
    return [
        {
            "title": r.get("title", ""),
            "snippet": r.get("description", ""),
            "url": r.get("link", ""),
        }
        for r in organic
        if r.get("title") or r.get("description")
    ]


# ── Recon / grounding discovery (SERP + Web Unlocker) ─────────────
_IDENTITY_PLATFORMS = {
    "linkedin": "linkedin.com/company",
    "crunchbase": "crunchbase.com/organization",
    "g2": "g2.com",
    "wikipedia": "wikipedia.org/wiki",
    "wikidata": "wikidata.org",            # the machine-readable knowledge-graph anchor AI engines ingest
    "x": "x.com",
}
_REVIEW_HOSTS = ("g2.com", "capterra.com", "trustpilot.com", "softwareadvice.com")


def discover_pages(domain: str, max_pages: int = 5) -> list[str]:
    """Discover the client's key content pages via `site:` SERP (products/solutions/platform/pricing/about).
    Returns absolute URLs on the client's own domain, deduped and capped; [] on failure."""
    root = (domain or "").split("://")[-1].strip("/").split("/")[0]
    if not root:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for topic in ("products", "solutions", "platform", "pricing", "about"):
        if len(urls) >= max_pages:
            break
        try:
            results = _call_serp_api(f"site:{root} {topic}")
        except Exception:
            results = []
        for r in results[:2]:
            u = r.get("url") or ""
            key = u.lower().rstrip("/")
            if root in u and key and key not in seen:
                seen.add(key)
                urls.append(u)
                if len(urls) >= max_pages:
                    break
        time.sleep(0.4)
    return urls


def _identity_matches(company_name: str, domain: str, url: str, title: str) -> bool:
    """True when a SERP result verifiably refers to THIS company — not merely the right host. Matches against the URL +
    TITLE only (the snippet echoes the search query and causes false accepts). Accepts on the client's own domain-core,
    a glued multi-token company name in the slug, or every significant name token as a WHOLE word — so wrong entities
    ('multiplier-solutions' for 'Multiplier AI', 'x.com/JasperDolphin' for 'Jasper') are rejected, real ones kept."""
    import re
    blob = re.sub(r"[^a-z0-9]+", " ", f"{title} {url}".lower())
    words = set(blob.split())
    compact = blob.replace(" ", "")
    host = (domain or "").split("://")[-1].split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    dcore = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
    if len(dcore) >= 4 and dcore in compact:            # the client's own domain in the result = strongest confirmation
        return True
    toks = [t for t in re.sub(r"[^a-z0-9]+", " ", (company_name or "").lower()).split() if len(t) >= 2]
    if not toks:
        return False
    if len(toks) >= 2 and "".join(toks) in compact:     # a glued multi-token name in the slug (e.g. 'multiplierai')
        return True
    return all(t in words for t in toks)                # every significant name token as a WHOLE word (no prefix collision)


def find_identity_urls(company_name: str, domain: str = "") -> dict:
    """SERP for the company's authoritative profiles; return {platform: url} ONLY when a result host matches the
    expected platform host AND the result verifiably refers to this company (see _identity_matches). Host-only matching
    surfaced wrong entities (e.g. 'crunchbase/multiplier-solutions' for 'Multiplier AI'); verified URLs only -> sameAs."""
    out: dict = {}
    if not company_name:
        return out
    for platform, host in _IDENTITY_PLATFORMS.items():
        try:
            results = _call_serp_api(f'"{company_name}" {platform}')
        except Exception:
            results = []
        host_root = host.split("/")[0]
        for r in results:
            u = r.get("url") or ""
            if host_root in u and _identity_matches(company_name, domain, u, r.get("title", "")):
                out[platform] = u
                break
        time.sleep(0.3)
    return out


def find_reviews(company_name: str) -> dict:
    """Find a grounded rating: SERP to a review-site page, scrape it, extract rating_value + review_count.
    Returns {} when nothing groundable is found — never fabricates a rating (D12)."""
    if not company_name:
        return {}
    try:
        results = _call_serp_api(f'"{company_name}" reviews rating G2 OR Capterra OR Trustpilot')
    except Exception:
        return {}
    for r in results:
        u = r.get("url") or ""
        if any(h in u for h in _REVIEW_HOSTS):
            try:
                html = _request_web_unlocker(u)
            except Exception:
                continue
            rating = _extract_rating(html)
            if rating:
                return {**rating, "source_url": u}
    return {}


def _extract_rating(html: str) -> dict | None:
    """Best-effort rating from a review page (schema.org ratingValue or 'X out of 5' + 'N reviews').
    Returns None unless a plausible rating_value in [1,5] is found (never guesses)."""
    import re
    text = _extract_text(html or "")
    out: dict = {}
    m = re.search(r'"ratingValue"\s*:\s*"?([0-5](?:\.\d+)?)"?', html or "")
    if not m:
        m = re.search(r'\b([1-4]\.\d|5(?:\.0)?)\s*(?:out of\s*5|/\s*5|stars)\b', text, re.IGNORECASE)
    if m:
        try:
            rv = float(m.group(1))
            if 1.0 <= rv <= 5.0:
                out["rating_value"] = rv
        except ValueError:
            pass
    rc = re.search(r'([\d,]{2,})\s+reviews', text, re.IGNORECASE)
    if rc:
        with contextlib.suppress(ValueError):
            out["review_count"] = int(rc.group(1).replace(",", ""))
    return out if out.get("rating_value") else None


def find_competitor_events(competitor_name: str, cluster_label: str, shift_type: str = "gain",
                           max_events: int = 3) -> list[dict]:
    """Ground a competitor's SOV shift with real, cited announcements via SERP.
    Returns [{event, source_url, excerpt}] (real results only); [] when nothing found -> caller uses a plausible cause."""
    year = date.today().year
    queries = [
        f'"{competitor_name}" {cluster_label} launch OR announcement {year}',
        f'"{competitor_name}" acquisition OR partnership OR funding {year}',
        f'"{competitor_name}" press release {cluster_label}',
    ]
    events: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        if len(events) >= max_events:
            break
        try:
            results = _call_serp_api(q)
        except Exception:
            continue
        for r in results[:3]:
            u = r.get("url") or ""
            if not u or u in seen or not r.get("title"):
                continue
            seen.add(u)
            events.append({"event": r.get("title", ""), "source_url": u, "excerpt": r.get("snippet", "")})
            if len(events) >= max_events:
                break
        time.sleep(0.5)
    return events


def _extract_text(html: str) -> str:
    """Strip script/style/nav/footer/header/aside tags from HTML and return collapsed-whitespace text.
    Uses BeautifulSoup (html.parser) lazily so the import cost is only paid when scraping is used."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())
