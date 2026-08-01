# generate.py — Tier 3: generation orchestrator + registration (router, facts, hard well-formedness gate).
# Long-form classes route 'unavailable' (content mirror absent — never fabricated). Registration
# PATCHES the Tier-2 target row via the enforced transition (content_url untouched until verify).
import json
import logging

from scout.builders.authority_gen import (
    generate_ai_bots_directives,
    generate_llms_txt,
    validate_llms_txt,
    validate_robots_directives,
)
from scout.builders.prose import fill_prose
from scout.builders.schema_gen import generate_schema_jsonld, validate_jsonld

log = logging.getLogger(__name__)

_LONG_FORM = {"blog", "case_study", "landing_page"}
_FLAG_BY_CLASS = {
    "schema_jsonld": "builder_schema_gen_enabled",
    "faq_page": "builder_schema_gen_enabled",
    "llms_txt": "builder_llms_gen_enabled",
    "ai_bots_allowlist": "builder_aibots_gen_enabled",
    "robots_txt": "builder_aibots_gen_enabled",
}


def route_brief(brief: dict, cfg) -> str:
    cls = brief.get("asset_class", "")
    if cls in _LONG_FORM:
        return "unavailable"        # content_tracking/blog_generations not mirrored — Phase B
    flag = _FLAG_BY_CLASS.get(cls)
    if flag is None:
        return "unavailable"
    return "scout_native" if getattr(cfg, flag, False) else "flag_off"


def assemble_facts(sb, client_name: str, domain: str, cfg, *, client_id: str = "", scrape_fn=None) -> dict:
    """Grounded fields only (D16): name + domain always; the client's facts-of-record (products, services,
    differentiators, sameAs, serviceType, areaServed, description) from recon_agent_onboarding when present;
    a raw scraped-cache description only as a last resort. Missing facts are omitted, never fabricated."""
    facts: dict = {"company_name": client_name or "", "domain": domain or ""}
    # Recon profile — the client's own substance. This is what un-starves the generators.
    if client_id:
        try:
            from scout.db.client_context import get_client_profile
            for k, v in (get_client_profile(sb, client_id) or {}).items():
                if v:
                    facts[k] = v
        except Exception as e:
            log.warning("[generate] client profile for %s degraded: %s", client_id, e)
    # Scraped-cache description as a fallback only when recon supplied none.
    try:
        from scout.db.geo_cache import get_scraped_page
        ttl = getattr(cfg, "geo_scraped_cache_ttl_days", 14)
        page = get_scraped_page(domain, ttl_days=ttl) if domain else None
        if page and page.get("content"):
            content = str(page["content"])
            facts.setdefault("description", content[:400].strip())
            facts["scraped_excerpt"] = content[:3000]
        elif scrape_fn and getattr(cfg, "builder_fact_enrichment_enabled", False) and domain:
            page = scrape_fn(domain) or {}
            content = str(page.get("content", ""))
            if content:
                facts.setdefault("description", content[:400].strip())
                facts["scraped_excerpt"] = content[:3000]
    except Exception as e:
        log.warning("[generate] facts assembly for %s degraded: %s", domain, e)
    return facts


def _existing_schema_types(sb, client_id) -> set[str]:
    """@types the client already publishes on its own site (report_data.schema_data) — skip these so the
    handoff never ships duplicate/competing markup. Best-effort; returns an empty set on any miss."""
    if not client_id:
        return set()
    try:
        from scout.db import sed_mapping as mm
        resp = (sb.table(mm.REPORT_DATA_TABLE)
                .select(f"{mm.REPORT_DATA_COLS['schema_data']},{mm.REPORT_DATA_COLS['created_at']}")
                .eq(mm.REPORT_DATA_COLS["client_id"], str(client_id))
                .order(mm.REPORT_DATA_COLS["created_at"], desc=True).limit(1).execute())
        rows = resp.data
        if not isinstance(rows, list) or not rows:
            return set()
        data = rows[0].get(mm.REPORT_DATA_COLS["schema_data"])
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return set()
        found = data.get("found") or data.get("types") or data.get("schema_types")
        if isinstance(found, dict):
            found = list(found.keys())
        return {str(t) for t in found if t} if isinstance(found, list) else set()
    except Exception:
        return set()


def _faq_questions(sb, brief: dict, limit: int = 5) -> list[str]:
    """FAQPage questions are REAL cluster-registry queries (demand-grounded, never invented)."""
    try:
        from scout.db.cluster_registry import fetch_cluster_registry
        registry = fetch_cluster_registry(sb, brief.get("client_id", "")) or {}
        entry = registry.get(brief.get("cluster_id", "")) or {}
        return [q for q in (entry.get("queries") or []) if q][:limit]
    except Exception as e:
        log.warning("[generate] cluster queries fetch failed: %s", e)
        return []


def generate_for_brief(sb, brief: dict, rec_row: dict, cfg, *,
                       synthesis_fn=None, scrape_fn=None) -> dict | None:
    route = route_brief(brief, cfg)
    if route != "scout_native":
        log.info("[generate] brief %s skipped: %s", brief.get("id"), route)
        return None
    cls = brief.get("asset_class", "")
    facts = assemble_facts(sb, rec_row.get("client_name", ""),
                           _domain_of(brief.get("target", "")), cfg,
                           client_id=brief.get("client_id", ""), scrape_fn=scrape_fn)
    provenance: dict = {"asset_class": cls, "generator": "deterministic_template",
                        "brief_id": brief.get("id"), "facts_keys": sorted(facts)}

    if cls == "schema_jsonld":
        wanted = (brief.get("seed_signals") or {}).get("schema_types_missing") or []
        existing = _existing_schema_types(sb, brief.get("client_id"))
        # Page-topic context so BreadcrumbList/Service are target-specific, not generic company stubs.
        schema_facts = {**facts, "cluster_label": brief.get("cluster_label") or "",
                        "target_url": brief.get("target") or ""}
        blocks, refused, skipped = [], [], []
        for t in wanted:
            if t in existing:            # client already publishes this @type — never ship a duplicate
                skipped.append(t)
                continue
            block = generate_schema_jsonld(t, schema_facts)
            if block is None:
                refused.append(t)
                continue
            ok, reason = validate_jsonld(block, t)
            if not ok:
                log.warning("[generate] malformed %s JSON-LD blocked: %s", t, reason)
                refused.append(t)
                continue
            blocks.append(block)
        if not blocks:
            log.info("[generate] no schema blocks for brief %s (refused=%s skipped_existing=%s)",
                     brief.get("id"), refused, skipped)
            return None
        provenance["schema_types"] = [b["@type"] for b in blocks]
        provenance["refused_types"] = refused
        if skipped:
            provenance["skipped_existing_types"] = skipped
        return {"payload": json.dumps(blocks, indent=2), "provenance": provenance}

    if cls == "faq_page":
        questions = _faq_questions(sb, brief)
        pairs = []
        prose_meta: list[dict] = []
        for q in questions:
            answer, meta = fill_prose("faq_answer", facts, {**brief, "question": q}, rec_row, cfg,
                                      synthesis_fn=synthesis_fn, scrape_fn=scrape_fn)
            prose_meta.append({"question": q, **meta})
            if answer:
                pairs.append({"question": q, "answer": answer})
        if not pairs:
            log.info("[generate] faq_page refused: no answerable grounded questions (prose off or no queries)")
            return None
        block = generate_schema_jsonld("FAQPage", {**facts, "faqs": pairs})
        ok, reason = validate_jsonld(block, "FAQPage")
        if not ok:
            log.warning("[generate] malformed FAQPage blocked: %s", reason)
            return None
        provenance.update({"generator": "template+prose", "prose": prose_meta,
                           "schema_types": ["FAQPage"]})
        return {"payload": json.dumps([block], indent=2), "provenance": provenance}

    if cls == "llms_txt":
        prose, meta = fill_prose("llms_summary", facts, brief, rec_row, cfg,
                                 synthesis_fn=synthesis_fn, scrape_fn=scrape_fn)
        payload = generate_llms_txt(facts, prose_summary=prose)
        ok, reason = validate_llms_txt(payload)
        if not ok:
            log.warning("[generate] malformed llms.txt blocked: %s", reason)
            return None
        provenance.update({"generator": "template+prose" if prose else "deterministic_template",
                           "prose": meta})
        return {"payload": payload, "provenance": provenance}

    if cls in ("ai_bots_allowlist", "robots_txt"):
        payload = generate_ai_bots_directives()
        ok, reason = validate_robots_directives(payload)
        if not ok:
            log.warning("[generate] malformed robots directives blocked: %s", reason)
            return None
        return {"payload": payload, "provenance": provenance}

    return None


def _domain_of(target: str) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(target).netloc or target
    except (ValueError, AttributeError):
        return target


def register_generated_asset(sb, brief: dict, payload: str, provenance: dict) -> tuple[str | None, str]:
    """Register a generation onto the Tier-2 target row (patch via enforced transition — never upsert).
    A brief without a source row inserts fresh, then patches the new row's lifecycle fields."""
    from scout.db import asset_writer as aw
    extra = {
        "asset_source": "built",
        "drafted_payload": payload,
        "content_provenance": provenance,
        "build_brief_id": brief.get("id"),
        "target": brief.get("target"),
        "revenue_opportunity_usd": brief.get("revenue_opportunity_usd"),
        "revenue_basis": brief.get("revenue_basis"),
    }
    asset_id = brief.get("scout_asset_id")
    if not asset_id:
        rows = [{"client_id": brief.get("client_id"), "recommendation_id": brief.get("recommendation_id"),
                 "cluster_id": brief.get("cluster_id"), "cluster_label": brief.get("cluster_label"),
                 "asset_source": "target", "asset_type": brief.get("asset_class"),
                 "content_url": None, "asset_status": "proposed",
                 "derived_from": brief.get("seed_signals") or {}}]
        if aw.write_scout_assets(sb, rows) < 1:
            return None, "fresh asset insert failed"
        try:
            from scout.db import sed_mapping as m
            resp = (sb.table(m.SCOUT_ASSETS_TABLE).select("id")
                    .eq("client_id", str(brief.get("client_id")))
                    .eq("recommendation_id", str(brief.get("recommendation_id")))
                    .eq("asset_type", brief.get("asset_class"))
                    .eq("content_url", "").limit(1).execute())
            asset_id = (resp.data or [{}])[0].get("id")
        except Exception as e:
            return None, f"fresh asset lookup failed: {e}"
        if not asset_id:
            return None, "fresh asset not found after insert"
    ok, reason = aw.transition_asset(sb, asset_id, "generated", extra_patch=extra)
    if not ok:
        # Regenerating onto a still-ungated 'generated' asset is a harmless payload refresh:
        # no gate has judged it yet, so patch directly instead of stalling the brief forever.
        if "'generated' -> 'generated'" in reason:
            if not aw.patch_scout_asset(sb, asset_id, extra):
                return None, "regeneration refresh patch failed"
        else:
            return None, reason
    if brief.get("id"):
        aw.update_build_brief(sb, brief["id"], {"status": "generated"})
    return asset_id, ""
