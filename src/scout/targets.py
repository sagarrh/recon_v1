# targets.py — validate the pages and queries a recommendation claims to target (pure, no I/O).
# Purpose: turn free-text LLM output into a measurable target set, or refuse it. A recommendation
#          whose targets cannot be validated is not measurable, and must say so rather than guess.
# Scope: pure functions over a client record + candidate strings. No DB, no network, no LLM.
# Consumers: scout/nodes/recommendation_gen.py (post-LLM validation), scout/db/sed_writer.py (persist).
#
# The load-bearing rule: a target page must be on the CLIENT'S OWN domain. Competitor and
# third-party citation URLs are evidence about the world, never something the client can change or
# something we may later match against their GA4 landing pages. Attributing a third party's traffic
# to a client's action is the exact error this whole direction exists to stop.
from urllib.parse import urlsplit, urlunsplit

# What kind of intervention this is. Drives nothing automated yet; it is the grouping key for
# "which recommendation types actually work" once outcomes accumulate.
ACTION_TYPES = frozenset({
    "content_update",   # change an existing client page
    "new_page",         # publish a page that does not exist yet
    "schema",           # structured data on a client page
    "ai_access",        # llms.txt / robots.txt / ai-bots allowlist
    "third_party",      # earn a mention or listing off-site
    "measurement",      # instrument something before it can be judged
    "other",
})
DEFAULT_ACTION_TYPE = "other"

# How confidently this recommendation is tied to something measurable.
MAPPING_EXACT = "exact"                # at least one validated client-owned page
MAPPING_QUERY_ONLY = "query_only"      # queries but no page — GSC-measurable, not GA4-measurable
MAPPING_UNMAPPED = "unmapped"          # nothing measurable; outcome measurement will not run

MAX_TARGET_PAGES = 10
MAX_TARGET_QUERIES = 25


def _bare_host(value: str) -> str:
    """Hostname with scheme, credentials, port, path and a leading 'www.' removed."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"//{raw}"
    host = urlsplit(raw).hostname or ""
    return host.rstrip(".").removeprefix("www.")


def owned_domains(client: dict) -> frozenset[str]:
    """Domains the client controls, from their registered domain and website.

    Both fields are operator-entered and arrive in inconsistent forms ('client.com',
    'https://www.client.com/', trailing whitespace), so each is reduced to a bare host."""
    candidates = (
        (client or {}).get("company_domain"),
        (client or {}).get("company_website"),
    )
    return frozenset(host for host in (_bare_host(c) for c in candidates) if host)


def is_owned(host: str, owned: frozenset[str]) -> bool:
    """True when `host` is an owned domain or a subdomain of one.

    Subdomains count (blog.client.com belongs to the client); suffix matching does not
    (notclient.com must never match client.com)."""
    host = _bare_host(host)
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in owned)


def _canonical_page(value: str, owned: frozenset[str], website_root: str) -> tuple[str | None, str]:
    """Return (canonical_url, reason). canonical_url is None when the page must be rejected.

    The canonical form is deliberately narrow — https, no 'www.', no query, no fragment, no
    trailing slash — so that one page has exactly one identity. Anything looser and the same page
    arrives twice under two spellings, which would later double-count it against GA4."""
    raw = (value or "").strip()
    if not raw:
        return None, "empty"

    # A bare path is a common and reasonable LLM answer; resolve it against the client's own site.
    if raw.startswith("/"):
        if not website_root:
            return None, "relative_path_without_known_client_website"
        raw = website_root.rstrip("/") + raw

    if "://" not in raw:
        raw = f"https://{raw}"

    try:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        hostname = parts.hostname or ""
    except ValueError:
        return None, "unparseable"
    if scheme not in {"http", "https"}:
        return None, "unsupported_scheme"
    host = _bare_host(hostname)
    if not host:
        return None, "no_hostname"
    if not is_owned(host, owned):
        return None, "not_client_owned"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    # Scheme is forced to https and 'www.' dropped so http/https and www/apex spellings of one page
    # collapse to a single target. Both joins downstream (GSC page, GA4 landing_page) compare on
    # host+path and ignore scheme, so nothing is lost by normalizing it.
    return urlunsplit(("https", host, path, "", "")), "ok"


def validate_target_pages(
    values, client: dict
) -> tuple[list[str], list[dict[str, str]]]:
    """Split candidate target pages into (accepted, rejected-with-reason).

    Accepted pages are canonical, deduplicated and capped. Rejection is not a failure — an
    unmapped recommendation is still publishable, it simply cannot be measured at page level."""
    owned = owned_domains(client)
    website_root = ((client or {}).get("company_website") or "").strip()
    if website_root and "://" not in website_root:
        website_root = f"https://{website_root}"

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values or []:
        canonical, reason = _canonical_page(str(value), owned, website_root)
        if canonical is None:
            rejected.append({"url": str(value), "reason": reason})
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        if len(accepted) < MAX_TARGET_PAGES:
            accepted.append(canonical)
        else:
            rejected.append({"url": str(value), "reason": "over_target_page_cap"})
    return accepted, rejected


def validate_target_queries(values) -> list[str]:
    """Normalize candidate target queries: trimmed, collapsed, deduplicated, capped.

    Queries are not validated against a source — GSC mapping happens at measurement time, where
    the closed mapping-status set decides whether each one is usable."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = " ".join(str(value).split()).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= MAX_TARGET_QUERIES:
            break
    return out


def normalize_action_type(value) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return text if text in ACTION_TYPES else DEFAULT_ACTION_TYPE


def resolve_mapping_confidence(pages: list[str], queries: list[str]) -> str:
    """Grade how measurable this recommendation is. Pages outrank queries: a page supports both
    GSC and GA4 measurement, whereas a query alone supports only GSC."""
    if pages:
        return MAPPING_EXACT
    if queries:
        return MAPPING_QUERY_ONLY
    return MAPPING_UNMAPPED


def apply_targets(raw: dict, client: dict) -> dict:
    """Validate one recommendation's raw target fields into the persisted shape.

    Returns the validated fields plus `target_rejections`, which is kept so an operator can see
    *why* a recommendation ended up unmapped instead of having to infer it."""
    pages, rejected = validate_target_pages(raw.get("target_pages"), client)
    queries = validate_target_queries(raw.get("target_queries"))
    return {
        "target_pages": pages,
        "target_queries": queries,
        "action_type": normalize_action_type(raw.get("action_type")),
        "mapping_confidence": resolve_mapping_confidence(pages, queries),
        "target_rejections": rejected,
    }
