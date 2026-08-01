# gap_recommendations.py — derive standing client-side GEO gap recommendations (recon + readiness + competitive).
# A DETECTED gap (a missing recon fact, a site-readiness gap, or a competitor asset the client lacks) becomes a
# client action. Pure + deterministic; detectors return ClientGapRecommendation and merge in derive_client_gaps.
# Consumers: facts_recon.build_client_profile (stores on the recon profile), export + report_gen (surfacing).
import logging

from scout.builders.competitive_narrative import (
    competitive_narrative,  # noqa: F401  (back-compat re-export)
)
from scout.models.investigation import ClientGapRecommendation

log = logging.getLogger(__name__)

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _has_contact(cp) -> bool:
    return isinstance(cp, dict) and any(cp.get(k) for k in ("telephone", "email", "url"))


# ── Detector 1: recon-gap. Data-driven — a rule is a dict; adding a gap = adding a row, not a function. ──
_RECON_GAP_RULES = [
    {"code": "no_reviews", "area": "reviews", "priority": "high",
     "title": "Establish a reviewed third-party profile (G2 / Capterra / Trustpilot)",
     "why": "AI answer engines weight third-party review volume heavily; with no groundable rating your "
            "AggregateRating schema cannot be built and you are far less likely to be recommended.",
     "action": "Claim and populate a G2/Capterra/Trustpilot profile, grow reviews to a credible volume, then add "
               "the profile URL to Organization.sameAs.",
     "when": lambda r: r.get("rating_value") is None},
    {"code": "thin_products", "area": "content_demand", "priority": "high",
     "title": "Publish detailed product / solution pages",
     "why": "Named product pages are the citable substance answer engines extract and attribute; few or thin "
            "products means little for an engine to quote about you.",
     "action": "Publish a dedicated, detailed page per offering (name, capabilities, proof) so each can be marked "
               "up as Product/Service.",
     "when": lambda r: len(r.get("products") or []) < 2 or r.get("confidence") == "low"},
    {"code": "no_logo", "area": "entity_authority", "priority": "medium",
     "title": "Publish a canonical Organization logo",
     "why": "Organization.logo feeds Google's Knowledge Panel and how answer engines render/attribute your brand; "
            "a stable logo is also an entity-disambiguation cue across profiles.",
     "action": "Expose a crawlable og:image and add Organization.logo (absolute URL) to your JSON-LD.",
     "when": lambda r: not r.get("logo_url")},
    {"code": "no_contact_point", "area": "entity_authority", "priority": "medium",
     "title": "Publish a machine-readable ContactPoint / NAP",
     "why": "A reachable, consistent contact point is a trust signal AI uses when deciding whether to name a vendor.",
     "action": "Add Organization.contactPoint (telephone/email/contact URL) to your JSON-LD; keep NAP consistent "
               "across profiles.",
     "when": lambda r: not _has_contact(r.get("contact_point"))},
    {"code": "no_area_served", "area": "structured_data", "priority": "medium",
     "title": "State the regions you serve",
     "why": "areaServed lets answer engines match you to location-qualified buyer queries; without it you are "
            "invisible to 'in <region>' questions.",
     "action": "State served regions on your pages so Service.areaServed can be emitted.",
     "when": lambda r: not r.get("area_served")},
    {"code": "thin_service_type", "area": "structured_data", "priority": "medium",
     "title": "Clarify your primary service category",
     "why": "A specific serviceType makes your Service node match category queries instead of reading as generic.",
     "action": "State your primary service category clearly so Service.serviceType is specific.",
     "when": lambda r: not r.get("service_type")},
    {"code": "thin_description", "area": "structured_data", "priority": "low",
     "title": "Publish a clear company description",
     "why": "A crisp factual description is what answer engines paraphrase when they name you; a missing one means "
            "they describe you vaguely or not at all.",
     "action": "Publish a 1-2 sentence factual description and an llms.txt summary.",
     "when": lambda r: not r.get("description")},
]


def _host_of(url: str) -> str:
    h = (url or "").lower().split("://")[-1].split("/")[0]
    return h[4:] if h.startswith("www.") else h


def _host_present(platform_host: str, have_hosts: set) -> bool:
    """True when a canonical platform host is covered by an existing sameAs host — exact or a subdomain of it
    (so 'en.wikipedia.org' covers 'wikipedia.org' but 'xyzx.com' does NOT cover 'x.com')."""
    return any(h == platform_host or h.endswith("." + platform_host) for h in have_hosts)


def _sameas_gap(row: dict) -> ClientGapRecommendation | None:
    """Per-platform sameAs gap: which canonical identity hosts are missing from the recon same_as[] (Wikidata/LinkedIn/
    Crunchbase raise priority to high). Reuses bright_data._IDENTITY_PLATFORMS as the single source of hosts."""
    from scout.integrations.bright_data import _IDENTITY_PLATFORMS
    have_hosts = {_host_of(u) for u in (row.get("same_as") or [])}
    missing = [p for p, host in _IDENTITY_PLATFORMS.items() if not _host_present(host.split("/")[0], have_hosts)]
    if not missing:
        return None
    priority = "high" if ({"linkedin", "crunchbase", "wikidata"} & set(missing)) else "medium"
    return ClientGapRecommendation(
        code="sameas_missing", area="entity_authority", priority=priority,
        title=f"Claim the missing identity profiles: {', '.join(missing)}",
        gap_signal=f"recon same_as[] is missing hosts for: {', '.join(missing)}",
        why_ai_visibility="sameAs is the primary edge answer engines and the knowledge graph use to resolve and "
                          "verify your company as a real entity; each missing profile (especially Wikidata/LinkedIn/"
                          "Crunchbase) leaves the entity unanchored and less citable.",
        client_action=f"Create/claim {', '.join(missing)} and add every real profile URL to Organization.sameAs.",
        source="recon", detail={"missing_platforms": missing})


def recon_gaps(row: dict) -> list:
    """Detector 1 — client-side GEO gaps derived from a recon_agent_onboarding row's missing/thin fields (pure)."""
    out: list = []
    sg = _sameas_gap(row)
    if sg:
        out.append(sg)
    for rule in _RECON_GAP_RULES:
        if rule["when"](row):
            out.append(ClientGapRecommendation(
                code=rule["code"], area=rule["area"], priority=rule["priority"], title=rule["title"],
                gap_signal=f"recon rule '{rule['code']}' matched (empty/thin field)",
                why_ai_visibility=rule["why"], client_action=rule["action"], source="recon"))
    return out


# ── Detector 2: readiness/completeness. Reuses ClientReadiness (nodes/client_readiness.py) — no new site fetch. ──
_AI_ACCESS_RULES = [
    {"code": "no_ai_bots", "area": "ai_access", "priority": "high", "flag": "ai_bots_present",
     "title": "Explicitly allow AI answer-engine crawlers",
     "why": "If GPTBot / ClaudeBot / PerplexityBot / Google-Extended are not allowed, ChatGPT, Claude, Perplexity "
            "and Google AI Overviews cannot read your site and will not cite you.",
     "action": "Add explicit Allow rules for GPTBot, ClaudeBot, PerplexityBot and Google-Extended in robots.txt."},
    {"code": "no_llms_txt", "area": "ai_access", "priority": "medium", "flag": "llms_txt_present",
     "title": "Publish an llms.txt",
     "why": "llms.txt gives answer engines a curated, machine-readable summary of what you do and which pages matter.",
     "action": "Publish /llms.txt summarizing your company, offerings and key URLs."},
    {"code": "no_robots_txt", "area": "ai_access", "priority": "low", "flag": "robots_present",
     "title": "Publish a robots.txt",
     "why": "robots.txt is the baseline crawl-control file engines look for; its absence reads as an unmanaged site.",
     "action": "Publish /robots.txt that allows the crawlers you want and points to your sitemap."},
]

# Recommended @type -> (priority, prerequisite recon fact | None, action). A prereq absent means the fact itself is
# missing, so the recon rec (e.g. no_reviews) already covers the root cause — we don't ask to mark up a nonexistent fact.
_SCHEMA_TYPE_RECS = {
    "Organization": ("high", None, "Publish an Organization JSON-LD block (name, url, logo, sameAs)."),
    "AggregateRating": ("high", "rating_value", "Add AggregateRating JSON-LD sourced from your reviews."),
    "Service": ("medium", None, "Mark up your core offerings as Service JSON-LD."),
    "FAQPage": ("medium", None, "Publish an FAQPage JSON-LD answering the top buyer questions."),
    "Product": ("medium", "products", "Mark up your products as Product JSON-LD."),
    "Review": ("medium", "rating_value", "Publish Review JSON-LD for your testimonials."),
    "WebSite": ("low", None, "Add a WebSite JSON-LD block with a SearchAction."),
    "BreadcrumbList": ("low", None, "Add BreadcrumbList JSON-LD to key pages."),
    "LocalBusiness": ("low", None, "If you operate a physical location, add LocalBusiness JSON-LD with a postal address."),
}

_GAP_PENALTY = {"high": 14, "medium": 7, "low": 3}


def _fact_present(row: dict, key: str) -> bool:
    v = row.get(key)
    return v is not None and v != [] and v != "" and v != {}


def readiness_gaps(readiness, row: dict) -> list:
    """Detector 2 — AI-access + schema-markup gaps from a ClientReadiness (the recon row supplies schema prerequisites)."""
    if readiness is None:
        return []
    out: list = []
    for rule in _AI_ACCESS_RULES:
        if not getattr(readiness, rule["flag"], False):
            out.append(ClientGapRecommendation(
                code=rule["code"], area=rule["area"], priority=rule["priority"], title=rule["title"],
                gap_signal=f"readiness: {rule['flag']} is False",
                why_ai_visibility=rule["why"], client_action=rule["action"], source="readiness"))
    for t in getattr(readiness, "schema_types_missing", None) or []:
        spec = _SCHEMA_TYPE_RECS.get(t)
        if not spec:
            continue
        priority, prereq, action = spec
        if prereq and not _fact_present(row, prereq):     # can't mark up a fact you don't have — recon rec covers it
            continue
        out.append(ClientGapRecommendation(
            code=f"schema_missing_{t.lower()}", area="structured_data", priority=priority,
            title=f"Add {t} structured data",
            gap_signal=f"readiness: {t} absent from site schema @types",
            why_ai_visibility=f"{t} JSON-LD is a schema.org type answer engines use to understand and cite your site; "
                              f"its absence leaves that signal on the table.",
            client_action=action, source="readiness"))
    return out


def geo_readiness_score(gaps) -> int:
    """0-100 GEO readiness score: 100 minus weighted penalties per open gap (high=14, medium=7, low=3), floored at 0.
    Accepts either ClientGapRecommendation objects or their dumped dicts."""
    def _prio(g):
        return g["priority"] if isinstance(g, dict) else g.priority
    return max(0, 100 - sum(_GAP_PENALTY.get(_prio(g), 0) for g in gaps))


# ── Detector 3: competitive parity. Pure over collected signals; live collection is the flag-gated collector below. ──
def competitive_gaps(row: dict, competitor_signals) -> list:
    """Detector 3 — parity gaps vs collected competitor signals (pure). Each signal is
    {name, rating_value?, review_count?, rating_source?, same_as?:[...]}. Aggregated so N competitors yield <=2 recs.
    Both recs carry area='competitive' so they group apart from the client's own recon reviews/entity gaps."""
    if not competitor_signals:
        return []
    from scout.integrations.bright_data import _IDENTITY_PLATFORMS
    out: list = []
    if row.get("rating_value") is None:
        rated = sorted({c.get("name") for c in competitor_signals if c.get("rating_value") is not None and c.get("name")})
        if rated:
            names = ", ".join(rated)
            out.append(ClientGapRecommendation(
                code="competitor_reviewed", area="competitive", priority="high",
                title="Ranked competitors are reviewed where you are absent",
                gap_signal=f"{names} carry third-party ratings; client rating_value is null",
                why_ai_visibility="When competitors carry third-party ratings and you carry none, answer engines have a "
                                  "comparable trust signal for them and nothing for you — they get recommended, you get omitted.",
                client_action=f"Claim and grow a G2/Capterra/Trustpilot profile to match the review presence of {names}.",
                source="competitive", detail={"competitors": rated}))
    # only recommend canonical identity platforms (never a random scraped host a competitor happens to link to)
    canonical = {h.split("/")[0] for h in _IDENTITY_PLATFORMS.values()}
    client_hosts = {_host_of(u) for u in (row.get("same_as") or [])}
    missing = sorted({h for c in competitor_signals for u in (c.get("same_as") or [])
                      if (h := _host_of(u)) in canonical and not _host_present(h, client_hosts)})
    if missing:
        out.append(ClientGapRecommendation(
            code="competitor_identity_edge", area="competitive", priority="medium",
            title="Ranked competitors hold identity profiles you lack",
            gap_signal=f"competitors are listed on {missing}; client sameAs lacks them",
            why_ai_visibility="Each authoritative profile a competitor holds and you don't is one more corroborating "
                              "source the knowledge graph and answer engines use to prefer them as the resolved entity.",
            client_action=f"Establish profiles on {', '.join(missing)} to close the identity gap.",
            source="competitive", detail={"missing_hosts": missing}))
    return out


def collect_competitor_signals(competitor_names, cfg) -> list[dict]:
    """Flag-gated Bright Data collection of competitor parity signals (grounded rating + identity URLs). Real SERP/scrape
    results only (D12/D16); returns [] and makes ZERO Bright Data calls when gap_competitive_enabled is off. Capped by
    gap_competitive_max. Feed the result to competitive_gaps()."""
    if not getattr(cfg, "gap_competitive_enabled", False):
        return []
    from scout.integrations import bright_data as bd
    cap = getattr(cfg, "gap_competitive_max", 2)
    names = [n for n in dict.fromkeys(competitor_names or []) if n][:cap]
    out: list[dict] = []
    for name in names:
        sig: dict = {"name": name}
        try:
            rev = bd.find_reviews(name)
            if rev.get("rating_value"):
                sig["rating_value"] = rev["rating_value"]
                sig["review_count"] = rev.get("review_count")
                sig["rating_source"] = rev.get("source_url")
        except Exception as e:
            log.warning("[gap] competitor reviews for %s failed: %s", name, e)
        try:
            ids = bd.find_identity_urls(name)
            if ids:
                sig["same_as"] = list(ids.values())
        except Exception as e:
            log.warning("[gap] competitor identity for %s failed: %s", name, e)
        out.append(sig)
    return out


def rank_gap_dicts(dicts: list) -> list[dict]:
    """Dedupe (by code) + priority-sort already-serialized gap dicts — used to merge stored recon gaps with
    freshly-derived readiness/competitive gaps at report time without reconstructing the models."""
    seen: set = set()
    out: list = []
    for g in dicts or []:
        code = g.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(g)
    out.sort(key=lambda g: _PRIORITY_ORDER.get(g.get("priority"), 3))
    return out


def derive_client_gaps(row: dict, readiness=None, competitor_signals=None) -> list[dict]:
    """Merge all detectors into a priority-sorted, deduped list of gap-recommendation dicts for the profile / surfacing.
    readiness/competitor_signals are None at recon time (recon-only gaps stored), set at cycle time for the full set."""
    gaps = recon_gaps(row) + readiness_gaps(readiness, row) + competitive_gaps(row, competitor_signals)
    return rank_gap_dicts([g.model_dump() for g in gaps])
