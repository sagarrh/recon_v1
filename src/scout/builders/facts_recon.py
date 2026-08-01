# facts_recon.py — Recon agent: build a client's structured facts-of-record from its OWN site.
# Purpose: Un-starve the Tier-3 asset builder. Scrapes the client's pages, extracts a grounded,
#          source-cited facts record via the LLM, and upserts it to recon_agent_onboarding (1:1 client).
# Scope: scrape (GEO cache first, then Bright Data) + LLM extract + defensive shaping + upsert. Never raises.
# Consumers: scripts/build_client_profile.py (CLI); read back by scout/db/client_context.get_client_profile.
import json
import logging
from datetime import UTC, datetime, timedelta

from scout.db import sed_mapping as m
from scout.utils import now_iso as _now_iso

log = logging.getLogger(__name__)

_NODE = "facts_recon"
_PROMPT = "scout-company-facts-extraction"
_LIST_FIELDS = ("products", "services", "area_served", "differentiators", "same_as")


def _norm_domain(s: str) -> str:
    """Bare domain for scraping/cache lookup: strip scheme + surrounding slashes ('https://www.x.com/' -> 'www.x.com')."""
    s = (s or "").strip().rstrip("/")
    if "://" in s:
        s = s.split("://", 1)[1]
    return s.strip("/")


def _norm(u: str) -> str:
    return (u or "").strip().lower().rstrip("/")


def resolve_client(sb, client_id: str) -> dict:
    """Return {company_name, company_domain, company_website, company_description, competitors} from onboarding, or {} on miss."""
    try:
        resp = (sb.table(m.ONBOARDING_TABLE)
                .select("company_name,company_domain,company_website,company_description,competitors")
                .eq("client_id", str(client_id)).limit(1).execute())
        rows = resp.data or []
        return rows[0] if rows else {}
    except Exception as e:
        log.warning("[facts_recon] onboarding lookup for %s failed: %s", client_id, e)
        return {}


def gather_pages(sb, domain: str, cfg, *, scrape_fn=None, page_urls=None) -> list[dict]:
    """Return [{url, content}] for the client: fresh GEO scrape cache for the homepage when available,
    else a live homepage scrape, plus any explicit page_urls — capped at recon_facts_max_pages. Never raises."""
    if not domain:
        return []
    pages: list[dict] = []
    seen: set[str] = set()
    ttl = getattr(cfg, "geo_scraped_cache_ttl_days", 14)
    max_pages = getattr(cfg, "recon_facts_max_pages", 4)

    try:
        from scout.db.geo_cache import get_scraped_page
        cached = get_scraped_page(domain, ttl_days=ttl)
    except Exception:
        cached = None
    if cached and cached.get("content"):
        url = cached.get("url") or domain
        pages.append({"url": url, "content": cached["content"]})
        seen.add(_norm(url))

    if scrape_fn is None:
        try:
            from scout.integrations.bright_data import scrape_website
            scrape_fn = scrape_website
        except Exception:
            scrape_fn = None

    targets: list[str] = []
    if not pages:                       # no cached homepage -> scrape it live
        targets.append(domain)
    targets.extend(page_urls or [])     # explicit product/solution pages (most relevant to the assets)
    if getattr(cfg, "recon_facts_deep", False):   # discover the client's key content pages via site: SERP
        try:
            from scout.integrations.bright_data import discover_pages
            targets.extend(discover_pages(domain, max_pages=max_pages))
        except Exception as e:
            log.warning("[facts_recon] page discovery for %s failed: %s", domain, e)

    for u in targets:
        if len(pages) >= max_pages or scrape_fn is None:
            break
        key = _norm(u)
        if not u or key in seen:
            continue
        try:
            page = scrape_fn(u) or {}
            content = str(page.get("content", "")).strip()
            if content:
                pages.append({"url": page.get("url") or u, "content": content})
                seen.add(key)
        except Exception as e:
            log.warning("[facts_recon] scrape %s failed: %s", u, e)
    return pages


def extract_facts(pages: list[dict], company_name: str, domain: str, *, synthesis_fn=None) -> dict:
    """Run the extraction prompt over the scraped pages and return the structured facts dict, or {} on failure/empty."""
    if not pages:
        return {}
    if synthesis_fn is None:
        from scout.llm import call_synthesis
        synthesis_fn = call_synthesis
    from scout.llm import load_prompt
    system = load_prompt(_PROMPT)
    payload = {
        "company_name": company_name,
        "domain": domain,
        "pages": [{"url": p.get("url"), "content": str(p.get("content", ""))[:6000]} for p in pages],
    }
    try:
        out = synthesis_fn(system, json.dumps(payload), expect_json=True, node_name=_NODE)
    except Exception as e:
        log.warning("[facts_recon] extraction failed: %s", e)
        return {}
    return out if isinstance(out, dict) else {}


def _shape_row(client_id: str, company_name: str, domain: str, facts: dict) -> dict:
    """Coerce the LLM facts dict into a recon_agent_onboarding row with safe types; empty stays empty (never fabricated)."""
    def _list(v):
        return v if isinstance(v, list) else []

    def _str(v):
        return v.strip() if isinstance(v, str) and v.strip() else None

    return {
        "client_id": str(client_id),
        "company_name": company_name or None,
        "domain": domain or None,
        "description": _str(facts.get("description")),
        "products": _list(facts.get("products")),
        "services": _list(facts.get("services")),
        "service_type": _str(facts.get("service_type")),
        "area_served": _list(facts.get("area_served")),
        "differentiators": _list(facts.get("differentiators")),
        "same_as": _list(facts.get("same_as")),
        "logo_url": _str(facts.get("logo_url")),
        "rating_value": (facts["rating_value"] if isinstance(facts.get("rating_value"), (int, float))
                         and not isinstance(facts.get("rating_value"), bool) else None),
        "review_count": (facts["review_count"] if isinstance(facts.get("review_count"), int)
                         and not isinstance(facts.get("review_count"), bool) else None),
        "rating_source": _str(facts.get("rating_source")),
        "contact_point": facts.get("contact_point") if isinstance(facts.get("contact_point"), dict) else None,
        "competitive_narrative": {},   # grounded LLM parity narrative; populated in build_client_profile when enabled
        "provenance": facts.get("provenance") if isinstance(facts.get("provenance"), dict) else {},
        "source": "recon_agent",
        "confidence": _str(facts.get("confidence")) or "low",
        "refreshed_at": _now_iso(),
    }


def profile_is_fresh(sb, client_id: str, ttl_days: int) -> bool:
    """True when a recon profile exists and was refreshed within ttl_days (so a re-run can skip it)."""
    try:
        resp = (sb.table(m.RECON_AGENT_ONBOARDING_TABLE)
                .select("refreshed_at").eq("client_id", str(client_id)).limit(1).execute())
        rows = resp.data or []
        ts = rows[0].get("refreshed_at") if rows else None
        if not ts:
            return False
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt >= datetime.now(UTC) - timedelta(days=ttl_days)
    except Exception as e:
        log.warning("[facts_recon] freshness check for %s failed: %s", client_id, e)
        return False


def upsert_profile(sb, row: dict) -> bool:
    """Upsert one recon_agent_onboarding row on client_id (1:1). Returns False on any error (logged, never raised)."""
    if not row.get("client_id"):
        return False
    try:
        sb.table(m.RECON_AGENT_ONBOARDING_TABLE).upsert(row, on_conflict="client_id").execute()
        return True
    except Exception as e:
        log.error("[facts_recon] upsert for %s FAILED: %s", row.get("client_id"), e, exc_info=True)
        return False


def _enrich_identity_reviews_logo(facts: dict, company_name: str, domain: str, cfg) -> None:
    """Bright Data enrichment (best-effort; mutates facts): sameAs identity URLs, a grounded rating, and the logo.
    Everything is a real SERP/scrape result — nothing is fabricated; each source is gated by its own flag."""
    from scout.integrations import bright_data as bd
    if getattr(cfg, "recon_identity_enabled", False):
        try:
            from scout.builders.gap_recommendations import _host_of, _host_present
            found = bd.find_identity_urls(company_name, domain)  # {platform: url}; domain anchors the match
            existing = list(facts.get("same_as") or [])
            have_hosts = {_host_of(u) for u in existing}
            for platform, url in found.items():                 # per-platform fill: add only platforms not already present
                host = bd._IDENTITY_PLATFORMS.get(platform, "").split("/")[0]
                if url and host and not _host_present(host, have_hosts):
                    existing.append(url)
                    have_hosts.add(_host_of(url))
            if existing:
                facts["same_as"] = existing
        except Exception as e:
            log.warning("[facts_recon] identity discovery failed: %s", e)
    if getattr(cfg, "recon_reviews_enabled", False) and facts.get("rating_value") is None:
        try:
            rev = bd.find_reviews(company_name)
            if rev.get("rating_value"):
                facts["rating_value"] = rev["rating_value"]
                if rev.get("review_count"):
                    facts["review_count"] = rev["review_count"]
                facts["rating_source"] = rev.get("source_url")
        except Exception as e:
            log.warning("[facts_recon] reviews discovery failed: %s", e)
    if getattr(cfg, "recon_facts_deep", False) and not facts.get("logo_url"):
        try:
            logo = bd.extract_logo(domain)
            if logo:
                facts["logo_url"] = logo
        except Exception as e:
            log.warning("[facts_recon] logo extraction failed: %s", e)


def build_client_profile(sb, client_id: str, cfg, *, page_urls=None, force: bool = False,
                         scrape_fn=None, synthesis_fn=None) -> dict | None:
    """Recon a client's facts: resolve -> gather pages -> extract -> upsert recon_agent_onboarding.
    Skips when a fresh profile exists (unless force). Returns the persisted row, or None on skip/failure."""
    if not getattr(cfg, "builder_facts_recon_enabled", False):
        log.info("[facts_recon] builder_facts_recon_enabled OFF — skipped")
        return None
    if not force and profile_is_fresh(sb, client_id, getattr(cfg, "recon_profile_ttl_days", 30)):
        log.info("[facts_recon] fresh profile for %s — skipped (pass force to refresh)", client_id)
        return None
    client = resolve_client(sb, client_id)
    if not client:
        log.warning("[facts_recon] no onboarding row for %s", client_id)
        return None
    company_name = client.get("company_name") or ""
    domain = _norm_domain(client.get("company_domain") or client.get("company_website") or "")
    pages = gather_pages(sb, domain, cfg, scrape_fn=scrape_fn, page_urls=page_urls)
    if not pages:
        log.warning("[facts_recon] no scrapable pages for %s (%s)", company_name, domain)
        return None
    facts = extract_facts(pages, company_name, domain, synthesis_fn=synthesis_fn)
    # onboarding.company_description is a reliable human-authored fallback for the description.
    if not facts.get("description") and client.get("company_description"):
        facts["description"] = client["company_description"]
    _enrich_identity_reviews_logo(facts, company_name, domain, cfg)   # Bright Data: sameAs, grounded rating, logo
    row = _shape_row(client_id, company_name, domain, facts)
    if getattr(cfg, "client_gap_recommendations_enabled", False):     # missing recon facts -> standing client GEO recs
        try:
            from scout.builders.gap_recommendations import (
                collect_competitor_signals,
                competitive_narrative,
                derive_client_gaps,
            )
            signals = None
            if getattr(cfg, "gap_competitive_enabled", False):        # flag-gated: real competitor rating/identity parity
                comps = client.get("competitors")
                signals = collect_competitor_signals(comps if isinstance(comps, list) else [comps], cfg)
            row["gaps"] = derive_client_gaps(row, competitor_signals=signals)
            if signals and getattr(cfg, "gap_competitive_narrative_enabled", False):   # grounded LLM parity narrative
                narr = competitive_narrative(row, signals, row["gaps"])
                if narr:                                              # only persist a grounded, non-empty narrative
                    row["competitive_narrative"] = narr
        except Exception as e:
            log.warning("[facts_recon] gap derivation failed: %s", e)
    if not upsert_profile(sb, row):
        return None
    log.info("[facts_recon] profile for %s: %d product(s), %d differentiator(s), %d gap(s), confidence=%s",
             company_name, len(row["products"]), len(row["differentiators"]), len(row.get("gaps") or []), row["confidence"])
    return row
