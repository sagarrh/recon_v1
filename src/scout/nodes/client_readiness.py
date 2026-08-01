# client_readiness.py — LangGraph node: client-site AI-readiness signal from GEO website-files + schema.
# Purpose: Surface whether the CLIENT's own site is AI-ready (llms.txt/ai_bots/robots + JSON-LD schema) so recommendations can name concrete gaps.
# Scope: One read-only ClientReadiness per client (deduped across triggers); reuses the cache dispatchers; flag-gated; never writes.
# Consumers: scout/graph.py registers this as the 4th investigation branch; recommendation_gen + report_gen read state['client_readiness'].
import logging

from scout.config import get_config
from scout.db.cache import get_geo_schema_types, get_geo_website_files
from scout.models.investigation import ClientReadiness
from scout.state import ScoutState

log = logging.getLogger(__name__)

# JSON-LD @types a service-business client site should expose for strong GEO/AI readiness.
GEO_RECOMMENDED_SCHEMA_TYPES = (
    "Organization", "LocalBusiness", "Service", "FAQPage",
    "Review", "AggregateRating", "BreadcrumbList", "WebSite", "Product",
)


def client_readiness_analysis(state: ScoutState) -> dict:
    """LangGraph node: build one ClientReadiness per client from GEO website-files + schema data.
    Dedupes triggers by client_id, is gated by geo_client_readiness_enabled, and returns {'client_readiness': {client_id: ClientReadiness}}."""
    if not get_config().geo_client_readiness_enabled:
        return {"client_readiness": {}}

    triggers = state.get("investigation_triggers", [])
    domain_by_client = {
        c.get("client_id"): (c.get("company_website") or c.get("company_domain") or "")
        for c in state.get("clients", [])
    }
    readiness: dict[str, ClientReadiness] = {}
    for trigger in triggers:
        client_id = getattr(trigger, "client_id", "")
        if not client_id or client_id in readiness:
            continue
        cr = _build_readiness(str(client_id), domain_by_client.get(client_id, ""))
        readiness[client_id] = cr
        print(f"[client_readiness] {client_id} -> confidence={cr.confidence}, missing_schema={len(cr.schema_types_missing)}")
    return {"client_readiness": readiness}


def _ai_access_from_snapshot(files: dict) -> dict:
    """Read AI-access presence from a GEO website_files_data blob via its {exists,...} sub-dicts — NOT the dict's truthiness.
    llms<-llm_txt.exists, robots<-robots_txt.exists, ai_bots<-robots_txt.ai_bots (nested); tolerates legacy bare/aliased shapes."""
    lt = files.get("llm_txt")
    rt = files.get("robots_txt")
    return {
        "llms": bool(lt.get("exists")) if isinstance(lt, dict) else bool(lt or files.get("llms_txt")),
        "robots": bool(rt.get("exists")) if isinstance(rt, dict) else bool(rt),
        "ai_bots": bool(rt.get("ai_bots")) if isinstance(rt, dict) else bool(files.get("ai_bots")),
    }


def _build_readiness(client_id: str, client_domain: str = "") -> ClientReadiness:
    """Shape a ClientReadiness from GEO's website_files_data `exists` flags + schema; live-scrape ONLY the gap (clients with no snapshot row, e.g. BDO).
    GEO's data is correct — the old code misread its {exists,...} sub-dicts as booleans; _ai_access_from_snapshot reads `exists` properly. Never raises."""
    files = None
    types = None
    try:
        files = get_geo_website_files(client_id)
    except Exception as e:
        log.warning("[client_readiness] website_files for %s failed: %s", client_id, e)
    try:
        types = get_geo_schema_types(client_id)
    except Exception as e:
        log.warning("[client_readiness] schema_types for %s failed: %s", client_id, e)

    # Trust GEO's already-computed `exists` flags when a snapshot row exists; live-scrape ONLY the gap.
    ai = None
    if files is not None:
        ai = _ai_access_from_snapshot(files)
    elif client_domain:
        live = None
        try:
            from scout.integrations.ai_access import fetch_ai_access_files
            live = fetch_ai_access_files(client_domain)
        except Exception as e:
            log.warning("[client_readiness] live AI-access fetch for %s failed: %s", client_domain, e)
        if live is not None:
            ai = {"llms": bool(live.get("llms_txt")), "robots": bool(live.get("robots_txt")), "ai_bots": bool(live.get("ai_bots"))}

    if ai is None and types is None:
        return ClientReadiness(
            client_id=client_id,
            readiness_summary="No GEO readiness data available for this client — website-files and schema both absent.",
            confidence="none",
        )

    ai = ai or {"llms": False, "robots": False, "ai_bots": False}
    llms_txt_present = ai["llms"]
    ai_bots_present = ai["ai_bots"]
    robots_present = ai["robots"]

    present = list(types or [])
    present_cf = {p.casefold() for p in present}
    missing = sorted(t for t in GEO_RECOMMENDED_SCHEMA_TYPES if t.casefold() not in present_cf)

    files_ok = llms_txt_present and ai_bots_present and robots_present
    confidence = "high" if (files_ok and present) else "low"

    return ClientReadiness(
        client_id=client_id,
        llms_txt_present=llms_txt_present,
        ai_bots_present=ai_bots_present,
        robots_present=robots_present,
        schema_types_present=present,
        schema_types_missing=missing,
        readiness_summary=_summarize(llms_txt_present, ai_bots_present, robots_present, present, missing),
        confidence=confidence,
    )


def _summarize(llms: bool, ai_bots: bool, robots: bool, present: list[str], missing: list[str]) -> str:
    """Render a deterministic, >=20-char readiness sentence covering AI-access files and schema coverage.
    Used as ClientReadiness.readiness_summary so the signal is human-readable without an LLM call."""
    files_have = [name for name, ok in (("llms.txt", llms), ("ai_bots", ai_bots), ("robots.txt", robots)) if ok]
    files_str = ", ".join(files_have) if files_have else "none"
    schema_str = ", ".join(present[:6]) if present else "none"
    missing_str = ", ".join(missing[:6]) if missing else "none"
    return (
        f"AI-access files present: {files_str}. "
        f"Schema @types present: {schema_str}. "
        f"Recommended schema missing: {missing_str}."
    )
