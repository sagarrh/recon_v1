# asset_attribution.py — Tier 2: deterministic asset<->revenue join (correlation only, no LLM).
# Purpose: Pair each scout_assets row with revenue via three channels (GA4 landing-page delta,
#          attribution_events client-level, selection_events AI-channel) + the Tier-1 modeled-share fallback.
# Scope: Read + join orchestration ONLY — every score/allocation is computed in scout/revenue.py (LOCKED),
#        every URL/cluster mapping in scout/db/revenue_context.py. Writes go through scout/db/asset_writer.py.
# Consumers: scripts/asset_attribution_report.py (CLI), scripts/weekly_run.py (run_asset_attribution).
import contextlib
import logging
from datetime import date, timedelta

from scout import revenue as R
from scout.config import get_config
from scout.db import revenue_context as rvx
from scout.db import sed_mapping as m
from scout.utils import parse_date as _parse_date

log = logging.getLogger(__name__)

ASSET_ATTRIBUTION_BASIS = {
    "ga4_landing_page":   "correlation (page-level GA4 revenue delta)",
    "attribution_events": "correlation (client-level converted revenue, not page-level)",
    "selection_events":   "correlation (AI-channel query->cluster)",
    "tier1_modeled":      "correlation (Tier-1 modeled opportunity share, not measured)",
    "none":               "no revenue basis (GA4/GSC/CRM absent for this client)",
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


def _fetch_recs(sb, rec_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(rec_ids), 100):
        chunk = rec_ids[i: i + 100]
        try:
            resp = (sb.table(m.SCOUT_RECOMMENDATIONS_TABLE)
                    .select("id,client_name,cluster_label,revenue_basis,revenue_opportunity_usd,revenue_at_risk_usd")
                    .in_("id", chunk).execute())
            for r in (resp.data or []):
                out[r.get("id")] = r
        except Exception as e:
            log.warning("[asset_attribution] recommendations fetch failed: %s", e)
    return out


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


def _fetch_attribution_events(sb, client_id: str, since=None) -> list[dict]:
    return rvx.get_attribution_revenue(sb, client_id, since=since)


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


def _row(asset, source, basis, usd, window_start, window_end, coverage, confidence, inputs):
    return {
        "scout_asset_id": asset.get("id"),
        "recommendation_id": asset.get("recommendation_id"),
        "client_id": asset.get("client_id"),
        "cluster_id": asset.get("cluster_id"),
        "revenue_source": source,
        "revenue_basis": basis,
        "attribution_basis": ASSET_ATTRIBUTION_BASIS[source if basis != "none" else "none"],
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


def _opportunity_usd(rec_meta: dict):
    """Opportunity-side dollar for a rec: offensive opportunity first, defensive at-risk fallback."""
    opp = rec_meta.get("revenue_opportunity_usd")
    return opp if opp is not None else rec_meta.get("revenue_at_risk_usd")


def _realized_usd(outcome: dict):
    """Realized-side dollar (D7): revenue protected when a defensive rec recovered, else the measured delta."""
    if not outcome:
        return None
    if outcome.get("recovered") and outcome.get("revenue_at_risk_usd") is not None:
        return outcome.get("revenue_at_risk_usd")
    return outcome.get("revenue_delta_usd")


def build_asset_attribution(sb, client_id=None, since=None) -> list[dict]:
    """One honesty-tagged attribution row per (asset, channel, window). Correlation only, never causation.
    Basis waterfall per cluster: GA4 page actual > attribution_events client actual > selection_events
    AI-channel actual > tier1_modeled opportunity share > none (NULL dollars). Cluster pools allocate to
    published assets only (D8); every asset ends with >=1 row so coverage KPIs have a denominator."""
    cfg = get_config()
    assets = _fetch_assets(sb, client_id)
    if not assets:
        return []

    # Tier-3 guardrail (FR-VERIFY-5): a BUILT asset may enter attribution only once it is verified
    # live AND the operator has opted in. Drafted/approved/handed-off assets never earn a dollar.
    link_ok = getattr(cfg, "builder_attribution_link_enabled", False)
    assets = [a for a in assets if a.get("asset_source") != "built"
              or (link_ok and a.get("lifecycle_status") == "verified")]
    if not assets:
        return []

    outcomes = _fetch_measured_outcomes(sb, client_id, since)
    outcome_by_cc: dict[tuple, dict] = {}
    for o in outcomes:
        key = (o.get("client_id"), o.get("cluster_id"))
        prev = outcome_by_cc.get(key)
        if prev is None or str(o.get("week_date") or "") > str(prev.get("week_date") or ""):
            outcome_by_cc[key] = o

    rec_ids = sorted({a.get("recommendation_id") for a in assets if a.get("recommendation_id")})
    recs = _fetch_recs(sb, rec_ids) if rec_ids else {}

    by_client: dict[str, list[dict]] = {}
    for a in assets:
        by_client.setdefault(a.get("client_id"), []).append(a)

    records: list[dict] = []
    weights = cfg.asset_attribution_channel_weights

    for cid, client_assets in by_client.items():
        sel_rows = _fetch_selection_events(sb, cid) if cid else []
        registry = _fetch_registry(sb, cid) if cid else {}
        ga4_rows = _fetch_ga4_rows(sb, cid) if (cid and cfg.geo_ga4_enabled) else []

        by_cluster: dict[str, list[dict]] = {}
        for a in client_assets:
            by_cluster.setdefault(a.get("cluster_id"), []).append(a)

        for clu, cluster_assets in by_cluster.items():
            outcome = outcome_by_cc.get((cid, clu))
            w_start = _parse_date((outcome or {}).get("week_date"))
            w_end = _parse_date((outcome or {}).get("window_elapsed_at"))
            w_weeks = int((outcome or {}).get("window_weeks") or 6)
            realized = _realized_usd(outcome)

            has_dollar_row: set[str] = set()

            # ---- Tier-1 gate: rec revenue_basis='none' => every row for that asset is none ----
            gated, active = [], []
            for a in cluster_assets:
                rec_meta = recs.get(a.get("recommendation_id")) or {}
                if a.get("recommendation_id") and rec_meta.get("revenue_basis", "none") == "none":
                    gated.append(a)
                else:
                    active.append(a)
            for a in gated:
                records.append(_row(a, "tier1_modeled", "none", None, w_start, w_end, None, None,
                                    {"basis_reason": "recommendation_revenue_basis_none"}))

            # ---- Channel A: GA4 landing-page delta (page-level actual; needs a measured window) ----
            page_attributed: dict[str, float] = {}
            if ga4_rows and w_start and w_end:
                for a in active:
                    if not a.get("content_url"):
                        continue
                    d = _ga4_page_delta(ga4_rows, a["content_url"], w_start, w_end, w_weeks)
                    if d is None:
                        continue
                    page_attributed[a["id"]] = d["delta"]
                    cov = R.coverage_score(d["delta"], realized if realized is not None
                                           else _opportunity_usd(recs.get(a.get("recommendation_id")) or {}))
                    conf = R.confidence_score(channel_weight=weights.get("ga4_landing_page", 1.0),
                                              coverage=cov, lag=1.0, is_modeled=False,
                                              modeled_discount=cfg.asset_modeled_discount)
                    records.append(_row(a, "ga4_landing_page", "actual", d["delta"], w_start, w_end, cov, conf, {
                        "basis_reason": "ga4_landing_page_window_delta",
                        "current_window_revenue": d["current"],
                        "baseline_window_revenue": d["baseline"],
                        "matched_ga4_rows": d["matched_rows"],
                        "normalized_url": rvx.normalize_url(a["content_url"]),
                    }))
                    has_dollar_row.add(a["id"])

            published = [a for a in active if a.get("asset_status") == "published"]
            eligible_ids = [a["id"] for a in published if a["id"] not in page_attributed]
            asset_by_id = {a["id"]: a for a in active}

            # ---- Channel C: selection_events AI-channel (already mirrored; cluster pool -> published) ----
            sel_total, sel_count, sel_no_rev = 0.0, 0, 0
            for ev in sel_rows:
                if not ev.get("was_selected"):
                    continue
                if rvx.cluster_for_query(registry, ev.get("query_text") or "") != clu:
                    continue
                rev = ev.get("revenue_attributed")
                if rev is None:
                    sel_no_rev += 1
                    continue
                try:
                    sel_total += float(rev)
                    sel_count += 1
                except (TypeError, ValueError):
                    pass
            if sel_count > 0 and eligible_ids:
                alloc = R.allocate_cluster_revenue(sel_total, eligible_ids, page_attributed)
                unallocated = sel_total - sum(v["usd"] for k, v in alloc.items() if k in eligible_ids)
                for aid in eligible_ids:
                    a = asset_by_id[aid]
                    usd = alloc[aid]["usd"]
                    cov = R.coverage_score(usd, realized if realized is not None else sel_total)
                    conf = R.confidence_score(channel_weight=weights.get("selection_events", 0.7),
                                              coverage=cov, lag=1.0, is_modeled=False,
                                              modeled_discount=cfg.asset_modeled_discount)
                    records.append(_row(a, "selection_events", "actual", usd, w_start, w_end, cov, conf, {
                        "basis_reason": f"selection_events; {alloc[aid]['basis_reason']}",
                        "selected_event_count": sel_count,
                        "selected_no_revenue_count": sel_no_rev,
                        "channel_total_usd": sel_total,
                        "unallocated_cluster_revenue": max(unallocated, 0.0),
                    }))
                    has_dollar_row.add(aid)
            elif sel_no_rev > 0:
                for a in (published or active):
                    records.append(_row(a, "selection_events", "none", None, w_start, w_end, None, None, {
                        "basis_reason": "selected_events_no_revenue_attributed",
                        "selected_no_revenue_count": sel_no_rev,
                    }))

            # ---- Channel B: attribution_events client-level (needs mirror + flag + window) ----
            if cfg.geo_crm_enabled and cid and w_start and w_end and eligible_ids:
                attr_rows = _fetch_attribution_events(sb, cid, since=w_start)
                in_window, pending = [], 0
                for ev in attr_rows:
                    ca = _parse_date(ev.get("converted_at"))
                    if ca is None:
                        continue
                    if ca <= w_end:
                        in_window.append(ev)
                    else:
                        pending += 1
                total = 0.0
                for ev in in_window:
                    with contextlib.suppress(TypeError, ValueError):
                        total += float(ev.get("revenue") or 0)
                if total > 0:
                    alloc = R.allocate_cluster_revenue(total, eligible_ids, page_attributed)
                    for aid in eligible_ids:
                        a = asset_by_id[aid]
                        pub = _parse_date(a.get("published_date"))
                        days = (w_end - pub).days if pub else 0
                        lag = R.lag_penalty(days, max_days=cfg.crm_lag_penalty_days,
                                            floor=cfg.crm_lag_penalty_floor)
                        usd = alloc[aid]["usd"]
                        cov = R.coverage_score(usd, realized if realized is not None else total)
                        conf = R.confidence_score(channel_weight=weights.get("attribution_events", 0.5),
                                                  coverage=cov, lag=lag, is_modeled=False,
                                                  modeled_discount=cfg.asset_modeled_discount)
                        records.append(_row(a, "attribution_events", "actual", usd, w_start, w_end, cov, conf, {
                            "basis_reason": f"attribution_events; {alloc[aid]['basis_reason']}",
                            "converted_event_count": len(in_window),
                            "pending_crm_lag_count": pending,
                            "channel_total_usd": total,
                            "lag_penalty_days": days,
                        }))
                        has_dollar_row.add(aid)

            # ---- tier1_modeled fallback: opportunity share for assets with no actual dollar ----
            by_rec: dict[str, list[dict]] = {}
            for a in active:
                if a["id"] in has_dollar_row or not a.get("recommendation_id"):
                    continue
                by_rec.setdefault(a["recommendation_id"], []).append(a)
            for rid, rec_assets in by_rec.items():
                opp = _opportunity_usd(recs.get(rid) or {})
                if opp is None:
                    continue
                alloc = R.allocate_cluster_revenue(opp, [a["id"] for a in rec_assets], {})
                for a in rec_assets:
                    usd = alloc[a["id"]]["usd"]
                    cov = R.coverage_score(usd, opp)
                    conf = R.confidence_score(channel_weight=weights.get("tier1_modeled", 0.4),
                                              coverage=cov, lag=1.0, is_modeled=True,
                                              modeled_discount=cfg.asset_modeled_discount)
                    records.append(_row(a, "tier1_modeled", "modeled", usd, w_start, w_end, cov, conf, {
                        "basis_reason": f"tier1_modeled_share; {alloc[a['id']]['basis_reason']}",
                        "rec_opportunity_usd": opp,
                    }))
                    has_dollar_row.add(a["id"])

            # ---- guarantee: every asset ends with >=1 row (coverage-KPI denominator) ----
            emitted_ids = {r["scout_asset_id"] for r in records}
            for a in active:
                if a["id"] not in emitted_ids:
                    records.append(_row(a, "tier1_modeled", "none", None, w_start, w_end, None, None,
                                        {"basis_reason": "no_revenue_channel_matched"}))

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
