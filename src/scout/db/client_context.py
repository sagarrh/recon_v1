# client_context.py — Read-only financials + co-mention context for one client (GEO onboarding/user_metrics/selection_events).
# Purpose: Supply revenue-framing financials and a competitor co-occurrence signal without re-asking or re-running monitoring.
# Scope: Stateless Supabase reads; selects ONLY allow-listed financial columns (never GA4/GSC tokens). In-memory only.
# Consumers: recommendation_gen (financials revenue framing), web_intelligence (co-mention signal).
import logging

from scout.db import sed_mapping as m
from scout.utils import safe_float

log = logging.getLogger(__name__)


def get_client_financials(sb, client_id: str) -> dict:
    """Return {average_order_value, conversion_rate, estimated_ctr, currency} for a client from onboarding (user_metrics fallback).
    Selects ONLY the allow-listed financial columns — never ga4/gsc token columns; returns {} on miss."""
    try:
        resp = (
            sb.table(m.ONBOARDING_TABLE)
            .select("average_order_value,conversion_rate,estimated_ctr,currency")
            .eq("client_id", str(client_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return _shape_financials(rows[0])
    except Exception as e:
        log.warning("[client_context] onboarding financials for %s failed: %s", client_id, e)
    try:
        resp = (
            sb.table(m.USER_METRICS_TABLE)
            .select("average_order_value,conversion_rate,estimated_ctr")
            .eq("client_id", str(client_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return _shape_financials(rows[0])
    except Exception as e:
        log.warning("[client_context] user_metrics financials for %s failed: %s", client_id, e)
    return {}


def _shape_financials(row: dict) -> dict:
    """Coerce a financials row into {average_order_value, conversion_rate, estimated_ctr, currency} with safe types.
    Numerics fall back to 0.0 on parse failure; currency defaults to 'USD' when absent (user_metrics has no currency)."""
    return {
        "average_order_value": safe_float(row.get("average_order_value"), 0.0),
        "conversion_rate": safe_float(row.get("conversion_rate"), 0.0),
        "estimated_ctr": safe_float(row.get("estimated_ctr"), 0.0),
        "currency": row.get("currency") or "USD",
    }


def get_client_profile(sb, client_id: str) -> dict:
    """Return the client's facts-of-record (description, products, services, service_type, area_served,
    differentiators, same_as, logo_url, contact_point) from recon_agent_onboarding, projected into the
    asset builder's vocabulary. Falls back to onboarding.company_description/target_region when no recon
    profile exists. Read-only; selects only the allow-listed profile columns; returns {} on miss."""
    try:
        resp = (
            sb.table(m.RECON_AGENT_ONBOARDING_TABLE)
            .select(",".join(m.RECON_PROFILE_COLS))
            .eq("client_id", str(client_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            shaped = _shape_profile(rows[0])
            if shaped:
                return shaped
    except Exception as e:
        log.warning("[client_context] recon profile for %s failed: %s", client_id, e)
    try:
        resp = (
            sb.table(m.ONBOARDING_TABLE)
            .select("company_description,target_region")
            .eq("client_id", str(client_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            out: dict = {}
            if rows[0].get("company_description"):
                out["description"] = rows[0]["company_description"]
            if rows[0].get("target_region"):
                out["area_served"] = [rows[0]["target_region"]]
            return out
    except Exception as e:
        log.warning("[client_context] onboarding description for %s failed: %s", client_id, e)
    return {}


def get_client_gaps(sb, client_id: str) -> list[dict]:
    """Return the stored recon-derived client GEO gap recommendations (recon_agent_onboarding.gaps), [] on miss.
    Read-only; used by recommendation_gen to surface standing client-side gaps alongside cycle findings."""
    try:
        resp = (
            sb.table(m.RECON_AGENT_ONBOARDING_TABLE)
            .select("gaps")
            .eq("client_id", str(client_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        gaps = rows[0].get("gaps") if rows else None
        return gaps if isinstance(gaps, list) else []
    except Exception as e:
        log.warning("[client_context] recon gaps for %s failed: %s", client_id, e)
        return []


def get_competitive_narrative(sb, client_id: str) -> dict:
    """Return the stored grounded LLM competitive-parity narrative (recon_agent_onboarding.competitive_narrative), {} on
    miss. Read-only; surfaced internally in the client export bundle (never pasted to the client verbatim)."""
    try:
        resp = (
            sb.table(m.RECON_AGENT_ONBOARDING_TABLE)
            .select("competitive_narrative")
            .eq("client_id", str(client_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        narr = rows[0].get("competitive_narrative") if rows else None
        return narr if isinstance(narr, dict) else {}
    except Exception as e:
        log.warning("[client_context] competitive narrative for %s failed: %s", client_id, e)
        return {}


def _shape_profile(row: dict) -> dict:
    """Project a recon_agent_onboarding row into the builder's facts vocabulary; omits empty values (never fabricated)."""
    def _list(v):
        return v if isinstance(v, list) and v else None

    def _str(v):
        return v.strip() if isinstance(v, str) and v.strip() else None

    out: dict = {}
    for key in ("description", "service_type", "logo_url"):
        val = _str(row.get(key))
        if val:
            out[key] = val
    for key in ("products", "services", "area_served", "differentiators", "same_as"):
        val = _list(row.get(key))
        if val:
            out[key] = val
    rv = row.get("rating_value")
    if isinstance(rv, (int, float)) and not isinstance(rv, bool):
        out["rating_value"] = rv                  # -> schema_gen AggregateRating/Review (grounded, D12-safe)
    rc = row.get("review_count")
    if isinstance(rc, int) and not isinstance(rc, bool):
        out["review_count"] = rc
    if isinstance(row.get("contact_point"), dict) and row["contact_point"]:
        out["contact_point"] = row["contact_point"]
    return out


def get_co_mentions(sb, client_id: str, query_or_cluster: str = "", limit: int = 200) -> dict[str, int]:
    """Return {competitor_name: count} aggregated from selection_events.competitors_mentioned (text[]) for a client.
    Optionally narrows to rows whose query_text ILIKE-matches query_or_cluster; returns {} on miss. Read-only."""
    try:
        resp = (
            sb.table(m.SELECTION_EVENTS_TABLE)
            .select("competitors_mentioned,query_text")
            .eq("client_id", str(client_id))
            .limit(limit)
            .execute()
        )
    except Exception as e:
        log.warning("[client_context] co_mentions for %s failed: %s", client_id, e)
        return {}
    needle = (query_or_cluster or "").strip().lower()
    counts: dict[str, int] = {}
    for row in resp.data or []:
        if needle and needle not in (row.get("query_text") or "").lower():
            continue
        mentions = row.get("competitors_mentioned") or []
        if not isinstance(mentions, list):
            continue
        for name in mentions:
            if name:
                key = str(name)
                counts[key] = counts.get(key, 0) + 1
    return counts
