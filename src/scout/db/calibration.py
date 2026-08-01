# calibration.py — Confidence-tier calibration over measured outcomes (no LLM).
# Purpose: Join scout_outcomes (executed) to recommendations.confidence and report SOV-recovery rate per confidence tier — the measured prior that displaces LLM-asserted confidence over time.
# Scope: Pure read over Supabase with a Python-side join (the client has no SQL join); min-sample guard flags low-volume tiers as insufficient rather than reporting a misleading ratio.
# Consumers: scripts/calibration_rollup.py (CLI) and R5-4 confidence feedback; runs on demand, not in the graph.
import logging
from datetime import date

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)

_TIERS = ("high", "medium", "low", "unknown")


def _fetch_executed_outcomes(sb, since: date | None) -> list[dict]:
    """Fetch scout_outcomes rows that were measured and executed (executed=True), optionally since a week_date.
    executed is NULL until analyst feedback exists, so this legitimately returns nothing until that signal lands."""
    try:
        q = sb.table(m.SCOUT_OUTCOMES_TABLE).select("recommendation_id,recovered,executed,week_date").eq("executed", True)
        if since is not None:
            q = q.gte("week_date", str(since))
        return q.execute().data or []
    except Exception as e:
        log.warning("[calibration] executed-outcomes fetch failed: %s", e)
        return []


def _fetch_rec_confidence(sb, rec_ids: list[str]) -> dict[str, str]:
    """Return {recommendation_id: confidence} by batch-reading recommendations for the given ids.
    Used to tier each executed outcome by the rec's self-reported confidence (Python-side join)."""
    out: dict[str, str] = {}
    for i in range(0, len(rec_ids), 100):
        chunk = rec_ids[i : i + 100]
        try:
            resp = sb.table(m.SCOUT_RECOMMENDATIONS_TABLE).select("id,confidence").in_("id", chunk).execute()
            for r in (resp.data or []):
                out[r.get("id")] = r.get("confidence")
        except Exception as e:
            log.warning("[calibration] confidence fetch failed: %s", e)
    return out


def compute_calibration(sb, since: date | None = None, min_n: int = 10) -> dict:
    """Return per-confidence-tier {n_executed, n_recovered, recovery_rate, insufficient} over executed, measured outcomes.
    Low-volume tiers (n_executed < min_n) report recovery_rate=None and insufficient=True so a 1-2 sample tier never reads as a real prior."""
    outcomes = _fetch_executed_outcomes(sb, since)
    rec_conf = _fetch_rec_confidence(sb, [o.get("recommendation_id") for o in outcomes if o.get("recommendation_id")])
    buckets = {t: {"n_executed": 0, "n_recovered": 0} for t in _TIERS}
    for o in outcomes:
        tier = rec_conf.get(o.get("recommendation_id"))
        if tier not in buckets:
            continue
        buckets[tier]["n_executed"] += 1
        if o.get("recovered"):
            buckets[tier]["n_recovered"] += 1
    result: dict[str, dict] = {}
    for tier, b in buckets.items():
        n = b["n_executed"]
        insufficient = n < min_n
        result[tier] = {
            "n_executed": n,
            "n_recovered": b["n_recovered"],
            "recovery_rate": None if insufficient else round(b["n_recovered"] / n, 4),
            "insufficient": insufficient,
        }
    return result


def _fetch_rec_types(sb, rec_ids: list[str]) -> dict[str, str]:
    """Return {recommendation_id: rec_type} (the offensive/defensive cause class) by batch-reading recommendations.
    Used by cause_type_accuracy to tier executed outcomes by their deterministic cause class (R5-4)."""
    out: dict[str, str] = {}
    for i in range(0, len(rec_ids), 100):
        chunk = rec_ids[i : i + 100]
        try:
            resp = sb.table(m.SCOUT_RECOMMENDATIONS_TABLE).select("id,rec_type").in_("id", chunk).execute()
            for r in (resp.data or []):
                out[r.get("id")] = r.get("rec_type")
        except Exception as e:
            log.warning("[calibration] rec_type fetch failed: %s", e)
    return out


def cause_type_accuracy(sb) -> dict:
    """Return measured {recovery_rate, n} per cause class (recommendation rec_type) over executed outcomes.
    Feeds R5-4's confidence feedback behind a volume gate; classes below the caller's sample floor are simply small-n."""
    outcomes = _fetch_executed_outcomes(sb, None)
    types = _fetch_rec_types(sb, [o.get("recommendation_id") for o in outcomes if o.get("recommendation_id")])
    buckets: dict[str, dict] = {}
    for o in outcomes:
        cls = types.get(o.get("recommendation_id"))
        if not cls:
            continue
        b = buckets.setdefault(cls, {"n": 0, "recovered": 0})
        b["n"] += 1
        if o.get("recovered"):
            b["recovered"] += 1
    return {
        cls: {"n": b["n"], "recovery_rate": (b["recovered"] / b["n"] if b["n"] else None)}
        for cls, b in buckets.items()
    }