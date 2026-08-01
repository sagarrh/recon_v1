# attribution.py — Labeled before/after SOV attribution over measured outcomes (no LLM, correlation only).
# Purpose: Turn each measured scout_outcomes row into an audit record showing client SOV before/after a recommendation plus the competitive field, explicitly labeled correlation (not causation).
# Scope: Pure read over scout_outcomes (+ recommendations for labels); no revenue figure, no Slack — this is an audit artifact. The field-adjusted counterfactual is deferred until volume exists.
# Consumers: scripts/attribution_report.py (CLI); runs on demand, not in the graph.
import logging
from datetime import date

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)

ATTRIBUTION_BASIS = "correlation (before/after)"


def _fetch_measured(sb, client_id: str | None, since: date | None) -> list[dict]:
    """Fetch scout_outcomes rows that have been measured (measured_at set), optionally scoped by client_id / week_date.
    The measured_at filter is applied in Python so the query stays portable across PostgREST client versions."""
    try:
        q = sb.table(m.SCOUT_OUTCOMES_TABLE).select("*")
        if client_id:
            q = q.eq("client_id", client_id)
        if since is not None:
            q = q.gte("week_date", str(since))
        rows = q.execute().data or []
    except Exception as e:
        log.warning("[attribution] outcomes fetch failed: %s", e)
        return []
    return [o for o in rows if o.get("measured_at")]


def _fetch_rec_labels(sb, rec_ids: list[str]) -> dict[str, dict]:
    """Return {recommendation_id: {client_name, competitor_name, cluster_label}} by batch-reading recommendations.
    Supplies human labels the scout_outcomes row does not store (cluster_label, client_name)."""
    out: dict[str, dict] = {}
    for i in range(0, len(rec_ids), 100):
        chunk = rec_ids[i : i + 100]
        try:
            resp = sb.table(m.SCOUT_RECOMMENDATIONS_TABLE).select("id,client_name,competitor_name,cluster_label").in_("id", chunk).execute()
            for r in (resp.data or []):
                out[r.get("id")] = r
        except Exception as e:
            log.warning("[attribution] rec-label fetch failed: %s", e)
    return out


def build_attribution(sb, client_id: str | None = None, since: date | None = None) -> list[dict]:
    """Return one labeled before/after attribution record per measured outcome, every row marked correlation (not causation).
    before=baseline_client_sov_pp, after=current_client_sov_pp, delta_pp, window_weeks, competitor/cluster labels, and the competitive field."""
    outcomes = _fetch_measured(sb, client_id, since)
    labels = _fetch_rec_labels(sb, [o.get("recommendation_id") for o in outcomes if o.get("recommendation_id")])
    records = []
    for o in outcomes:
        meta = labels.get(o.get("recommendation_id"), {})
        records.append({
            "recommendation_id": o.get("recommendation_id"),
            "client_id": o.get("client_id"),
            "client_name": meta.get("client_name"),
            "competitor_name": o.get("competitor_name") or meta.get("competitor_name"),
            "cluster_id": o.get("cluster_id"),
            "cluster_label": meta.get("cluster_label") or o.get("cluster_id"),
            "before": o.get("baseline_client_sov_pp"),
            "after": o.get("current_client_sov_pp"),
            "delta_pp": o.get("sov_delta_pp"),
            "window_weeks": o.get("window_weeks"),
            "field": o.get("field"),
            "attribution_basis": ATTRIBUTION_BASIS,
        })
    return records
