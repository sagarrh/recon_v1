# asset_registry.py — Tier 2: deterministic target-asset derivation + GEO content reads (no LLM).
# Purpose: Derive the assets a recommendation implies (from rec + client_readiness); read existing GEO content (Phase B).
# Scope: Pure derivation (no I/O); Phase-B reads are flag-gated and return [] on any miss. Rows are scout_assets-shaped.
# Consumers: scout/db/sed_writer.py (ship-time registration), scout/reports/asset_attribution.py, scripts/asset_attribution_report.py.
import logging
import re

log = logging.getLogger(__name__)

_SCHEMA_TYPE_TO_ASSET = {
    "faqpage": "faq_page",
    "review": "schema_jsonld",
    "aggregaterating": "schema_jsonld",
    "organization": "schema_jsonld",
    "localbusiness": "schema_jsonld",
    "service": "schema_jsonld",
    "breadcrumblist": "schema_jsonld",
    "website": "schema_jsonld",
}

_BULLET_KEYWORDS = [
    (re.compile(r"\bfaq\b", re.I), "faq_page"),
    (re.compile(r"\bcase[ -]stud(?:y|ies)\b", re.I), "case_study"),
    (re.compile(r"\b(?:landing[ -]page|service[ -]page)\b", re.I), "landing_page"),
    (re.compile(r"\b(?:blog|post|article|guide)\b", re.I), "blog"),
]


def _field(obj, name, default=None):
    """Read a field off a Pydantic model OR a plain dict (rec.client_readiness is a dict in-memory)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")


def derive_target_assets(rec, client_readiness, recommendation_id=None, run_id=None,
                         company_website: str = "") -> list[dict]:
    """Deterministic derivation of the target assets a recommendation implies (PRD §2.4 rules 1-4).
    Pure, no LLM, no I/O; idempotent (same inputs -> same dedupe keys). Returns scout_assets-shaped dicts."""
    cluster_id = _field(rec, "cluster_id", "") or ""
    cluster_label = _field(rec, "cluster_label", "") or ""
    client_id = _field(client_readiness, "client_id", "") or ""

    target_url, url_source = None, "none"
    site = (company_website or "").strip().rstrip("/")
    if site and "://" not in site:
        site = f"https://{site}"   # clients store bare domains; downstream origin/domain parsing needs a scheme
    slug = _slug(cluster_label)
    if site and slug:
        target_url, url_source = f"{site}/{slug}", "domain_slug"

    seen_types: set[str] = set()
    assets: list[dict] = []

    def _add(asset_type: str, derived_from: dict):
        if asset_type in seen_types:
            return
        seen_types.add(asset_type)
        derived_from["target_url"] = target_url
        derived_from["target_url_source"] = url_source
        assets.append({
            "client_id": client_id or None,
            "recommendation_id": recommendation_id,
            "run_id": run_id,
            "cluster_id": cluster_id,
            "cluster_label": cluster_label,
            "asset_source": "target",
            "asset_type": asset_type,
            "content_url": None,
            "content_title": None,
            "published_date": None,
            "geo_source_table": None,
            "geo_source_id": None,
            "generated_by_vibe_engine": None,
            "derived_from": derived_from,
            "asset_status": "proposed",
        })

    # Rule 1: missing recommended schema types -> ONE asset per family carrying ALL its gap types
    # (accumulating, not first-wins: a dedupe that dropped types starved the Tier-3 generator live).
    missing = [str(t) for t in (_field(client_readiness, "schema_types_missing", []) or [])]
    faq_types = [t for t in missing if _SCHEMA_TYPE_TO_ASSET.get(t.lower()) == "faq_page"]
    jsonld_types = [t for t in missing if _SCHEMA_TYPE_TO_ASSET.get(t.lower()) == "schema_jsonld"]
    if faq_types:
        _add("faq_page", {"schema_types_missing": faq_types})
    if jsonld_types:
        _add("schema_jsonld", {"schema_types_missing": jsonld_types})

    # Rule 2: AI-access gap -> llms_txt asset.
    llms = _field(client_readiness, "llms_txt_present", True)
    bots = _field(client_readiness, "ai_bots_present", True)
    if not llms or not bots:
        _add("llms_txt", {"ai_access_gap": True, "llms_txt_present": llms, "ai_bots_present": bots})

    # Rule 3: action-bullet content cues (fixed keyword map, first match per bullet).
    for bullet in (_field(rec, "action_bullets", []) or []):
        for pattern, asset_type in _BULLET_KEYWORDS:
            if pattern.search(bullet):
                _add(asset_type, {"action_bullets": [bullet]})
                break

    return assets
