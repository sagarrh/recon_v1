# outcome_measure.py — Deterministic weekly SOV outcome measurement for shipped recommendations (no LLM).
# Purpose: For each recommendation whose timeline window has elapsed, measure current cluster SOV vs the frozen R1-5 baseline and record the delta/recovery into scout_outcomes (in place).
# Scope: Pure compute over Supabase (recommendations + scout_outcomes + decision_log) and load_data(); upserts measurement columns onto the existing 'proposed' rows. No OpenRouter / no LLM.
# Consumers: scripts/weekly_run.py invokes run_outcome_measurement after a completed live run; not part of the LangGraph pipeline.
import logging
from datetime import date, timedelta

from scout.db import sed_mapping as m
from scout.utils import now_iso as _now_iso
from scout.utils import parse_date as _parse_date

log = logging.getLogger(__name__)

# Exact prose strings emitted by recommendation_gen._timeline → conservative UPPER bound of the week window.
_TIMELINE_WINDOW_WEEKS = {
    "monitoring next cycle — no timeline until cause is identified": 6,
    "act within 2 weeks while competitor is weakened": 2,
    "4-6 weeks (AI re-indexing cycle)": 6,
    "2-4 weeks post-implementation": 4,
    "4-8 weeks (third-party signal propagation)": 8,
    "4-8 weeks — low confidence, monitor closely": 8,
    "3-5 weeks": 5,
}
_DEFAULT_WINDOW_WEEKS = 6


def _window_weeks_from_timeline(timeline: str) -> int:
    """Map a recommendation's exact _timeline prose string to the conservative UPPER bound of its week window.
    Unknown/empty strings fall back to a 6-week default so an unmatched timeline still gets measured eventually."""
    return _TIMELINE_WINDOW_WEEKS.get((timeline or "").strip(), _DEFAULT_WINDOW_WEEKS)


def _fetch_timelines(sb, rec_ids: list[str]) -> dict[str, dict]:
    """Return {recommendation_id: {timeline, window_weeks}} by batch-reading the recommendations table.
    window_weeks is the structured measurement window (None on legacy rows → caller falls back to prose)."""
    out: dict[str, dict] = {}
    for i in range(0, len(rec_ids), 100):
        chunk = rec_ids[i : i + 100]
        try:
            resp = sb.table(m.SCOUT_RECOMMENDATIONS_TABLE).select("id,timeline,window_weeks").in_("id", chunk).execute()
            for r in (resp.data or []):
                out[r.get("id")] = {"timeline": r.get("timeline") or "", "window_weeks": r.get("window_weeks")}
        except Exception as e:
            log.warning("[outcome_measure] timeline fetch failed: %s", e)
    return out


def _current_client_sov_index() -> dict[tuple, float]:
    """Build {(client_id, cluster_id): client_sov_this_week} from a fresh load_data() bundle (the __client__ series).
    Reads the live SOV bundle once (no LLM) so each elapsed recommendation can read its current cluster SOV."""
    from scout.db.reader import load_data
    idx: dict[tuple, float] = {}
    bundle = load_data(None)
    for c in bundle.get("clients", []) or []:
        cid = c.get("client_id")
        for cl in c.get("clusters", []) or []:
            idx[(cid, cl.get("cluster_id"))] = cl.get("client_sov_this_week")
    return idx


def _decision_field(sb, run_id, cluster_id):
    """Return the `field` jsonb persisted in scout_decision_log for this run+cluster, or None when absent.
    Carries the competitive field alongside the outcome for attribution context (R5-3) without recomputing it."""
    try:
        resp = (
            sb.table(m.SCOUT_DECISION_LOG_TABLE)
            .select("field")
            .eq("run_id", run_id)
            .eq("cluster_id", cluster_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0].get("field") if rows else None
    except Exception:
        return None


def _measure_revenue(sb, outcome_row: dict) -> tuple[float | None, str]:
    """Measured GA4 revenue for this recommendation's target landing pages, with its category.

    Returns (None, 'unavailable') unless the recommendation carries mapped target pages: property-wide
    GA4 revenue is not this recommendation's revenue. CRM is out of scope, so for clients without GA4
    ecommerce data the honest answer here is permanently 'unavailable'."""
    from scout.config import get_config
    cfg = get_config()
    if not (cfg.revenue_outcome_enabled and cfg.geo_ga4_enabled):
        return None, "unavailable"

    pages = {p for p in (outcome_row.get("target_pages") or []) if p}
    if not pages:
        return None, "unavailable"
    try:
        from scout import revenue as R
        from scout.db import revenue_context as rvx
        handles = rvx.get_client_revenue_handles(sb, outcome_row.get("client_id", ""))
        ga4 = rvx.get_ga4_revenue(sb, handles.get("ga4_property_id", ""))
        normalized = {rvx.normalize_url(p) for p in pages}
        rev = R.actual_revenue_from_ga4(ga4_rows=ga4, landing_pages=normalized)
        category = R.resolve_revenue_category(
            page_scoped_revenue=rev,
            linkage=R.LINKAGE_EXACT_PAGE if rev is not None else R.LINKAGE_NONE,
        )
        return rev, str(category)
    except Exception as e:
        log.warning("[outcome_measure] revenue measurement failed for %s: %s",
                    outcome_row.get("recommendation_id"), e)
        return None, "unavailable"


def run_outcome_measurement(sb, today: date) -> int:
    """Measure SOV outcomes in place for every shipped recommendation whose timeline window has elapsed (deterministic, no LLM).
    Reads the FROZEN R1-5 baseline, computes current client-cluster SOV via load_data(), and upserts sov_delta_pp/recovered onto the existing scout_outcomes row."""
    try:
        resp = sb.table(m.SCOUT_OUTCOMES_TABLE).select("*").is_("measured_at", "null").execute()
        rows = resp.data or []
    except Exception as e:
        log.warning("[outcome_measure] scout_outcomes select failed: %s", e)
        return 0
    if not rows:
        return 0

    rec_ids = [r.get("recommendation_id") for r in rows if r.get("recommendation_id")]
    timelines = _fetch_timelines(sb, rec_ids)
    sov_idx = _current_client_sov_index()

    updates = []
    for r in rows:
        wd = _parse_date(r.get("week_date"))
        if wd is None:
            continue
        tl = timelines.get(r.get("recommendation_id")) or {}
        window_weeks = tl.get("window_weeks")
        if window_weeks is None:
            window_weeks = _window_weeks_from_timeline(tl.get("timeline", ""))
        window_elapsed_at = wd + timedelta(weeks=window_weeks)
        if window_elapsed_at > today:
            continue  # window not yet elapsed — measure on a later run

        baseline = r.get("baseline_client_sov_pp")
        current = sov_idx.get((r.get("client_id"), r.get("cluster_id")))
        delta = (current - baseline) if (current is not None and baseline is not None) else None
        recovered = (delta >= 0) if delta is not None else None

        rev_current, rev_category = _measure_revenue(sb, r)

        rev_delta = None
        if rev_current is not None and r.get("baseline_revenue_usd") is not None:
            try:
                from scout import revenue as R
                rev_delta = R.delta_revenue(
                    baseline_revenue=r.get("baseline_revenue_usd"),
                    current_revenue=rev_current,
                )
            except Exception as e:
                log.warning("[outcome_measure] revenue delta failed for %s: %s", r.get("recommendation_id"), e)

        updates.append({
            "recommendation_id": r.get("recommendation_id"),
            "run_id": r.get("run_id"),
            "client_id": r.get("client_id"),
            "cluster_id": r.get("cluster_id"),
            "competitor_name": r.get("baseline_primary_competitor"),
            "window_weeks": window_weeks,
            "window_elapsed_at": str(window_elapsed_at),
            "current_client_sov_pp": current,
            "sov_delta_pp": delta,
            "recovered": recovered,
            "executed": None,   # unknown without analyst signal (R5-2 filters on it); never fabricate
            "field": _decision_field(sb, r.get("run_id"), r.get("cluster_id")),
            "measured_at": _now_iso(),
            "current_revenue_usd": rev_current,
            "revenue_delta_usd": rev_delta,
            "revenue_category": rev_category,
        })

    if not updates:
        return 0
    try:
        sb.table(m.SCOUT_OUTCOMES_TABLE).upsert(updates, on_conflict="recommendation_id").execute()
    except Exception as e:
        log.warning("[outcome_measure] scout_outcomes upsert failed: %s", e)
        return 0
    return len(updates)
