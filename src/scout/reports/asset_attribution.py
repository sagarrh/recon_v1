# asset_attribution.py — Tier 2: deterministic asset<->revenue join (correlation only, no LLM).
# Purpose: Pair a scout_assets row with revenue ONLY where an observable link exists — today that is an
#          exact GA4 landing-page match. Every other asset is recorded as insufficient_linkage.
# Scope: Read + join orchestration ONLY — every score is computed in scout/revenue.py, every URL/cluster
#        mapping in scout/db/revenue_context.py. Writes go through scout/db/asset_writer.py.
# Non-goal: allocating a cluster revenue pool across assets. An equal split (or a winner-takes-all split
#        to the single published asset) is deterministic but commercially meaningless, so it is not done.
# Consumers: scripts/asset_attribution_report.py (CLI), scripts/weekly_run.py (run_asset_attribution).
import logging
from datetime import date, timedelta

from scout import revenue as R
from scout.config import get_config
from scout.db import revenue_context as rvx
from scout.db import sed_mapping as m
from scout.utils import parse_date as _parse_date

log = logging.getLogger(__name__)

ASSET_ATTRIBUTION_BASIS = {
    "ga4_landing_page":     "correlation (exact page-level GA4 revenue delta)",
    "insufficient_linkage": "no observable link between this asset and any revenue row",
}


# ---- fetchers (module-level so tests patch them directly) ----

def _fetch_assets(sb, client_id=None) -> list[dict]:
    try:
        q = sb.table(m.SCOUT_ASSETS_TABLE).select("*")
        if client_id:
            q = q.eq("client_id", str(client_id))
        return q.execute().data or []
    except Exception as e:
        log.warning("[asset_attribution] scout_assets fetch failed: %s", e)
        return []


def _fetch_measured_outcomes(sb, client_id=None, since=None) -> list[dict]:
    try:
        q = sb.table(m.SCOUT_OUTCOMES_TABLE).select("*")
        if client_id:
            q = q.eq("client_id", str(client_id))
        if since is not None:
            q = q.gte("week_date", str(since))
        rows = q.execute().data or []
        return [o for o in rows if o.get("measured_at")]
    except Exception as e:
        log.warning("[asset_attribution] scout_outcomes fetch failed: %s", e)
        return []


def _fetch_selection_events(sb, client_id: str) -> list[dict]:
    try:
        return (sb.table(m.SELECTION_EVENTS_TABLE)
                .select("query_text,was_selected,revenue_attributed")
                .eq("client_id", str(client_id)).execute().data or [])
    except Exception as e:
        log.warning("[asset_attribution] selection_events fetch for %s failed: %s", client_id, e)
        return []


def _fetch_registry(sb, client_id: str) -> dict:
    try:
        from scout.db.cluster_registry import fetch_cluster_registry
        return fetch_cluster_registry(sb, client_id) or {}
    except Exception as e:
        log.warning("[asset_attribution] cluster registry fetch for %s failed: %s", client_id, e)
        return {}


def _fetch_ga4_rows(sb, client_id: str, weeks: int = 26) -> list[dict]:
    handles = rvx.get_client_revenue_handles(sb, client_id)
    return rvx.get_ga4_revenue(sb, handles.get("ga4_property_id") or "", weeks=weeks)


# ---- channel computations (pure over fetched rows; math delegated to scout.revenue) ----

def _ga4_page_delta(ga4_rows, content_url, window_start: date, window_end: date,
                    window_weeks: int) -> dict | None:
    """Baseline-vs-current GA4 revenue delta for one landing page over the outcome window (D6).
    Returns None when no GA4 row matches the page in either window (no Channel-A row, never $0)."""
    key = rvx.normalize_url(content_url)
    if not key:
        return None
    baseline_start = window_start - timedelta(weeks=window_weeks)
    cur = base = 0.0
    cur_n = base_n = 0
    for row in (ga4_rows or []):
        if rvx.normalize_url(row.get("landing_page") or "") != key:
            continue
        d = _parse_date(row.get("metric_date"))
        rev = row.get("revenue")
        if d is None or rev is None:
            continue
        try:
            rev = float(rev)
        except (TypeError, ValueError):
            continue
        if window_start <= d <= window_end:
            cur += rev
            cur_n += 1
        elif baseline_start <= d < window_start:
            base += rev
            base_n += 1
    if cur_n == 0 and base_n == 0:
        return None
    return {"delta": cur - base, "current": cur, "baseline": base,
            "matched_rows": cur_n + base_n}


def _row(asset, source, category, usd, window_start, window_end, coverage, confidence, inputs):
    return {
        "scout_asset_id": asset.get("id"),
        "recommendation_id": asset.get("recommendation_id"),
        "client_id": asset.get("client_id"),
        "cluster_id": asset.get("cluster_id"),
        "revenue_source": source,
        "revenue_category": str(category),
        "attribution_status": ("attributed" if source == "ga4_landing_page"
                               else R.LINKAGE_NONE),
        "attribution_basis": ASSET_ATTRIBUTION_BASIS[source],
        "attributed_revenue_usd": usd,
        "currency_code": "USD",
        "window_start": str(window_start) if window_start else None,
        "window_end": str(window_end) if window_end else None,
        "coverage_score": coverage,
        "confidence_score": confidence,
        "revenue_inputs": inputs,
        "_asset_type": asset.get("asset_type"),
        "_content_url": asset.get("content_url"),
        "_cluster_label": asset.get("cluster_label"),
    }


def _unlinked_row(asset, reason, window_start, window_end, extra=None):
    """An asset with no observable revenue link. Dollars stay NULL — never 0, never an allocated share."""
    inputs = {"basis_reason": reason}
    if extra:
        inputs.update(extra)
    return _row(asset, "insufficient_linkage", R.RevenueCategory.unavailable, None,
                window_start, window_end, None, None, inputs)


def _realized_usd(outcome: dict):
    """Measured revenue delta for the outcome window, used only as a coverage denominator."""
    if not outcome:
        return None
    return outcome.get("revenue_delta_usd")


def _cluster_ai_channel_revenue(sel_rows, registry, cluster_id) -> dict:
    """AI-channel revenue observed for this cluster's queries. Recorded as context only.

    It maps a query to a cluster, never an event to an asset, so it can never license an asset-level
    dollar. Kept so the evidence is visible rather than silently dropped."""
    total, count, no_rev = 0.0, 0, 0
    for ev in sel_rows or []:
        if not ev.get("was_selected"):
            continue
        if rvx.cluster_for_query(registry, ev.get("query_text") or "") != cluster_id:
            continue
        rev = ev.get("revenue_attributed")
        if rev is None:
            no_rev += 1
            continue
        try:
            total += float(rev)
            count += 1
        except (TypeError, ValueError):
            no_rev += 1
    if count == 0 and no_rev == 0:
        return {}
    return {
        "cluster_ai_channel_revenue_observed": total if count else None,
        "cluster_ai_channel_event_count": count,
        "cluster_ai_channel_events_without_revenue": no_rev,
        "cluster_ai_channel_note": "observed at cluster level; not linkable to this asset",
    }


def _attributed_rows(active, ga4_rows, *, w_start, w_end, w_weeks, realized, weights, cfg):
    """Channel A — exact GA4 landing-page revenue delta. The only asset-level linkage we accept."""
    rows: list[dict] = []
    linked: set[str] = set()
    if not (ga4_rows and w_start and w_end):
        return rows, linked
    for a in active:
        if not a.get("content_url"):
            continue
        d = _ga4_page_delta(ga4_rows, a["content_url"], w_start, w_end, w_weeks)
        if d is None:
            continue
        cov = R.coverage_score(d["delta"], realized)
        conf = R.confidence_score(channel_weight=weights.get("ga4_landing_page", 1.0),
                                  coverage=cov, lag=1.0, is_modeled=False,
                                  modeled_discount=cfg.asset_modeled_discount)
        rows.append(_row(a, "ga4_landing_page", R.RevenueCategory.recorded, d["delta"],
                         w_start, w_end, cov, conf, {
                             "basis_reason": "ga4_landing_page_window_delta",
                             "current_window_revenue": d["current"],
                             "baseline_window_revenue": d["baseline"],
                             "matched_ga4_rows": d["matched_rows"],
                             "normalized_url": rvx.normalize_url(a["content_url"]),
                         }))
        linked.add(a["id"])
    return rows, linked


def _group_by(rows, key: str) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out


def _latest_outcome_by_cluster(outcomes) -> dict[tuple, dict]:
    """Newest measured outcome per (client, cluster) — it defines the measurement window."""
    out: dict[tuple, dict] = {}
    for o in outcomes:
        key = (o.get("client_id"), o.get("cluster_id"))
        prev = out.get(key)
        if prev is None or str(o.get("week_date") or "") > str(prev.get("week_date") or ""):
            out[key] = o
    return out


def _cluster_records(cluster_assets, *, outcome, ga4_rows, ai_channel, weights, cfg) -> list[dict]:
    """Attribution rows for one cluster: exact GA4 page matches earn dollars, everything else does not."""
    w_start = _parse_date((outcome or {}).get("week_date"))
    w_end = _parse_date((outcome or {}).get("window_elapsed_at"))
    w_weeks = int((outcome or {}).get("window_weeks") or 6)

    linked_rows, linked_ids = _attributed_rows(
        cluster_assets, ga4_rows, w_start=w_start, w_end=w_end, w_weeks=w_weeks,
        realized=_realized_usd(outcome), weights=weights, cfg=cfg,
    )
    reason = ("no ga4 landing-page match for this asset" if ga4_rows and w_start
              else "no measured outcome window or no ga4 rows for this client")
    unlinked = [_unlinked_row(a, reason, w_start, w_end, ai_channel)
                for a in cluster_assets if a["id"] not in linked_ids]
    return linked_rows + unlinked


def build_asset_attribution(sb, client_id=None, since=None) -> list[dict]:
    """One honesty-tagged attribution row per (asset, window). Correlation only, never causation.

    An asset earns a dollar only through an observable link — currently an exact GA4 landing-page match.
    Every other asset gets an insufficient_linkage row with NULL dollars, so coverage KPIs keep a
    denominator without inventing attribution. Cluster revenue is never split across assets."""
    cfg = get_config()
    assets = _fetch_assets(sb, client_id)

    # Tier-3 guardrail (FR-VERIFY-5): a BUILT asset may enter attribution only once it is verified
    # live AND the operator has opted in. Drafted/approved/handed-off assets never earn a dollar.
    link_ok = getattr(cfg, "builder_attribution_link_enabled", False)
    assets = [a for a in assets if a.get("asset_source") != "built"
              or (link_ok and a.get("lifecycle_status") == "verified")]
    if not assets:
        return []

    outcome_by_cc = _latest_outcome_by_cluster(_fetch_measured_outcomes(sb, client_id, since))
    weights = cfg.asset_attribution_channel_weights
    records: list[dict] = []

    for cid, client_assets in _group_by(assets, "client_id").items():
        sel_rows = _fetch_selection_events(sb, cid) if cid else []
        registry = _fetch_registry(sb, cid) if cid else {}
        ga4_rows = _fetch_ga4_rows(sb, cid) if (cid and cfg.geo_ga4_enabled) else []

        for clu, cluster_assets in _group_by(client_assets, "cluster_id").items():
            records.extend(_cluster_records(
                cluster_assets,
                outcome=outcome_by_cc.get((cid, clu)),
                ga4_rows=ga4_rows,
                ai_channel=_cluster_ai_channel_revenue(sel_rows, registry, clu),
                weights=weights,
                cfg=cfg,
            ))

    return records


def run_asset_attribution(sb) -> int:
    """Flag-gated build+persist for scripts/weekly_run.py. Returns rows written (0 when flags off).
    Deterministic, no LLM — the Tier-2 sibling of outcome_measure.run_outcome_measurement."""
    cfg = get_config()
    if not (cfg.revenue_layer_enabled and cfg.asset_attribution_enabled):
        return 0
    records = build_asset_attribution(sb)
    if not records:
        return 0
    from scout.db.asset_writer import write_asset_attribution
    return write_asset_attribution(sb, records)
