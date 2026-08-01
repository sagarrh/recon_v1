# sed_writer.py — Supabase write layer for live-mode pipeline artifacts.
# Purpose: Persists every Scout artifact (recommendations, reports, sov_tracking, triggers, investigations, blog detections, cycle_runs) into the user's Supabase project.
# Scope: Public write_outputs_to_sed dispatcher + per-table upsert helpers + cycle_runs lifecycle (start_run/finish_run); per-helper try/except so one failure doesn't block the rest.
# Consumers: scout/db/writer.py invokes write_outputs_to_sed() in live mode; run.py invokes start_run/finish_run for the cycle_runs lifecycle row.
import json
import logging

from scout.db import sed_mapping as m
from scout.keys import make_trigger_key
from scout.state import ScoutState
from scout.utils import now_iso as _now_iso

log = logging.getLogger(__name__)

_PRIORITY_MAP = {
    "urgent": "high",
    "standard": "medium",
    "opportunistic": "low",
}


def _to_jsonable(value):
    """Convert pydantic models, dicts, lists, and primitives into a JSON-serializable shape.
    Falls back to str(value) for anything not recognized so an unexpected type never blocks a write."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _client_id_lookup(state: ScoutState) -> dict[str, str]:
    """Build a {client_name: client_id} map from state['clients'] for resolving FKs.
    Empty dict when state has no clients; lookups against an absent name return None to the caller."""
    out: dict[str, str] = {}
    for c in state.get("clients", []) or []:
        name = c.get("client_name") or c.get("name") or ""
        cid = c.get("client_id") or c.get("id") or ""
        if name and cid:
            out[name] = cid
    return out


def _trigger_lookup(state: ScoutState) -> dict[str, dict]:
    """Build a {trigger_key: trigger_dict} map from state's investigation_triggers list.
    trigger_key format matches the report_gen / web_intelligence convention: '{competitor_name}::{cluster_id}'."""
    out: dict[str, dict] = {}
    for t in state.get("investigation_triggers", []) or []:
        td = t.model_dump(mode="json") if hasattr(t, "model_dump") else dict(t)
        key = make_trigger_key(td.get('client_id', ''), td.get('competitor_name', ''), td.get('cluster_id', ''))
        out[key] = td
    return out


def start_run(sb, run_id: str, sync_date: str, mode: str, client_filter: str | None) -> None:
    """Insert a cycle_runs row with status='started' so the operator can see the run is in flight.
    Idempotent on run_id (PK) — re-issued start_run for the same id no-ops via on_conflict."""
    row = {
        "run_id": run_id,
        "sync_date": str(sync_date),
        "mode": mode,
        "client_filter": client_filter or None,
        "status": "started",
        "started_at": _now_iso(),
    }
    try:
        sb.table(m.SCOUT_CYCLE_RUNS_TABLE).upsert(row, on_conflict="run_id").execute()
    except Exception as e:
        log.warning("[sed_writer] start_run failed for %s: %s", run_id, e)


_FINISH_RUN_OPTIONAL_FIELDS = (
    "clients_processed",
    "triggers_fired",
    "no_shift_clients",
    "capped_investigations",
    "blog_triggers_fired",
    "clusters_processed",
    "competitor_cluster_pairs_evaluated",
    "investigations_by_type",
    "no_significant_shifts_clients_list",
    "capped_investigations_list",
    "data_quality_errors",
    "quarantine_count",          # R1-4: count of quarantined recommendations this run
    "quarantine_by_reason",      # R1-4: jsonb {reason_bucket: count} of quarantine reasons this run
    "hallucinated_number_metric",  # R2-4: jsonb {node: {model,total_numerals,unbacked,rate}} numeric-provenance shadow metric
)


def finish_run(sb, run_id: str, status: str, totals: dict | None = None, error_message: str | None = None) -> None:
    """Mark a cycle_runs row as completed/failed with token totals, CycleSummary counters, and optional error_message.
    Subsumes the previous _write_cycle_summary helper — callers pass all CycleSummary fields directly in totals; missing keys are skipped."""
    totals = totals or {}
    patch: dict = {
        "status": status,
        "completed_at": _now_iso(),
        "total_tokens": int(totals.get("total_tokens", 0) or 0),
        "total_prompt_tokens": int(totals.get("total_prompt_tokens", 0) or 0),
        "total_completion_tokens": int(totals.get("total_completion_tokens", 0) or 0),
    }
    for field in _FINISH_RUN_OPTIONAL_FIELDS:
        if field in totals and totals[field] is not None:
            value = totals[field]
            if field in ("investigations_by_type",
                         "quarantine_by_reason",
                         "hallucinated_number_metric",
                         "no_significant_shifts_clients_list",
                         "capped_investigations_list"):
                patch[field] = _to_jsonable(value) or ([] if field.endswith("_list") else {})
            else:
                patch[field] = int(value) if isinstance(value, (int, float, bool)) else value
    if error_message:
        patch["error_message"] = error_message[:2000]
    try:
        sb.table(m.SCOUT_CYCLE_RUNS_TABLE).update(patch).eq("run_id", run_id).execute()
    except Exception as e:
        log.warning("[sed_writer] finish_run failed for %s: %s", run_id, e)
    if status == "completed":
        try:
            _token_spike_alert(sb, run_id, int(patch.get("total_tokens", 0) or 0))
        except Exception as e:
            log.warning("[sed_writer] token spike check failed for %s: %s", run_id, e)


def _token_spike_alert(sb, run_id: str, this_run_tokens: int) -> None:
    """Compare this run's total_tokens to the trailing average of the last N completed runs and post_alert on a spike.
    Lazy-imports config + post_alert (house pattern, avoids any import cycle); needs >=2 prior runs so a cold start can't false-fire (R1-6)."""
    from scout.config import get_config
    from scout.integrations.slack import post_alert
    cfg = get_config()
    n = int(getattr(cfg, "token_trailing_window_runs", 4) or 4)
    multiple = float(getattr(cfg, "token_trailing_multiple_alert", 2.3) or 2.3)
    resp = (
        sb.table(m.SCOUT_CYCLE_RUNS_TABLE)
        .select("run_id,total_tokens")
        .eq("status", "completed")
        .neq("run_id", run_id)
        .order("completed_at", desc=True)
        .limit(n)
        .execute()
    )
    prior = [int(r.get("total_tokens") or 0) for r in (resp.data or []) if r.get("total_tokens")]
    if len(prior) < 2:
        return
    trailing_avg = sum(prior) / len(prior)
    if trailing_avg <= 0 or this_run_tokens <= trailing_avg * multiple:
        return
    post_alert(
        f"Scout token spike — run {run_id[:8]}: {this_run_tokens:,} tokens vs "
        f"trailing avg {trailing_avg:,.0f} over {len(prior)} runs (>{multiple}x).",
        [{"type": "section", "text": {"type": "mrkdwn", "text": (
            f":money_with_wings: *Token spike* on run `{run_id[:8]}`\n"
            f"• This run: *{this_run_tokens:,}*\n"
            f"• Trailing avg ({len(prior)} runs): *{trailing_avg:,.0f}*\n"
            f"• Multiple: *{this_run_tokens / trailing_avg:.2f}x* (threshold {multiple}x)"
        )}}],
    )


def _write_sov_tracking(sb, state: ScoutState) -> int:
    """Upsert one sov_tracking row per SOVTrackingRecord, keyed on (run_id, client_id, cluster_id, competitor_name).
    Maps Pydantic field names to the table's column names (sov_score->current_sov, alert_type->alert_flag, etc.)."""
    run_id = state.get("run_id", "")
    rows = []
    for rec in state.get("sov_tracking_records", []) or []:
        d = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
        rows.append({
            "run_id": run_id,
            "client_id": d.get("client_id"),
            "cluster_id": d.get("cluster_id"),
            "competitor_name": d.get("competitor_name"),
            "week_date": str(d.get("week_date")) if d.get("week_date") else None,
            "current_sov": d.get("sov_score"),
            "rolling_mean": d.get("rolling_avg_4w"),
            "rolling_std": d.get("rolling_std_4w"),
            "z_score": d.get("z_score"),
            "sov_delta_pp": d.get("change_vs_avg"),
            "alert_flag": d.get("alert_type"),
            "is_significant": bool(d.get("alert_triggered", False)),
            "client_sov_this_week": d.get("client_sov_this_week"),
            "client_sov_change_vs_avg": d.get("client_sov_change_vs_avg"),
            "alert_reason": d.get("alert_reason"),
        })
    if not rows:
        return 0
    sb.table(m.SCOUT_SOV_TRACKING_TABLE).upsert(
        rows, on_conflict="run_id,client_id,cluster_id,competitor_name"
    ).execute()
    return len(rows)


def _write_investigation_triggers(sb, state: ScoutState) -> dict[str, str]:
    """Upsert investigation_triggers rows and return a {trigger_key: id} map for downstream FK linking.
    trigger_key is built as '{competitor_name}::{cluster_id}' to match the rest of the pipeline's convention."""
    run_id = state.get("run_id", "")
    sync_date = str(state.get("sync_date", ""))
    triggers = state.get("investigation_triggers", []) or []
    rows = []
    for t in triggers:
        d = t.model_dump(mode="json") if hasattr(t, "model_dump") else dict(t)
        trigger_key = make_trigger_key(d.get('client_id', ''), d.get('competitor_name', ''), d.get('cluster_id', ''))
        rows.append({
            "run_id": run_id,
            "client_id": d.get("client_id"),
            "trigger_key": trigger_key,
            "cluster_id": d.get("cluster_id"),
            "competitor_name": d.get("competitor_name"),
            "competitor_domain": d.get("competitor_domain"),
            "week_date": sync_date or None,
            "detection_source": d.get("detection_source") or "sov_detection",
            "alert_flag": d.get("shift_type"),
            "z_score": d.get("shift_in_sd_units"),
            "sov_delta_pp": d.get("shift_magnitude"),
            "blog_post_url": d.get("blog_post_url"),
            "blog_post_title": d.get("blog_post_title"),
            "client_name": d.get("client_name"),
            "cluster_label": d.get("cluster_label"),
            "shift_type": d.get("shift_type"),
            "shift_magnitude": d.get("shift_magnitude"),
            "shift_in_sd_units": d.get("shift_in_sd_units"),
            "client_sov_change": d.get("client_sov_change"),
            "correlated_displacement": bool(d.get("correlated_displacement", False)),
            "investigation_priority": d.get("investigation_priority"),
            "triage_reason": d.get("triage_reason"),
        })
    if not rows:
        return {}
    resp = sb.table(m.SCOUT_INVESTIGATION_TRIGGERS_TABLE).upsert(
        rows, on_conflict="run_id,trigger_key"
    ).execute()
    out: dict[str, str] = {}
    for r in (resp.data or []):
        key = r.get("trigger_key")
        rid = r.get("id")
        if key and rid:
            out[key] = rid
    return out


def _write_investigations(sb, state: ScoutState, trigger_id_map: dict[str, str], trigger_meta: dict[str, dict]) -> dict[tuple, str]:
    """Insert one investigations row per trigger_key and return a {(competitor_name, cluster_id): id} map.
    Downstream _write_recommendations consumes the map to populate the recommendations.investigation_id FK with real Supabase ids."""
    run_id = state.get("run_id", "")
    sync_date = str(state.get("sync_date", ""))
    wc_map = state.get("website_changes", {}) or {}
    sig_map = state.get("third_party_signals", {}) or {}
    ai_map = state.get("ai_citation_changes", {}) or {}
    keys = set(wc_map) | set(sig_map) | set(ai_map)
    rows = []
    row_keys: list[tuple] = []
    for key in keys:
        meta = trigger_meta.get(key, {})
        parts = key.split("::")
        comp = meta.get("competitor_name") or (parts[1] if len(parts) >= 3 else parts[0])
        clu = meta.get("cluster_id") or parts[-1]
        client_id = meta.get("client_id")
        row_keys.append((client_id, comp, clu))
        rows.append({
            "run_id": run_id,
            "trigger_id": trigger_id_map.get(key),
            "trigger_key": key,
            "client_id": client_id,
            "cluster_id": clu,
            "competitor_name": comp,
            "week_date": sync_date or None,
            "website_changes": _to_jsonable(wc_map.get(key)),
            "third_party_signals": _to_jsonable(sig_map.get(key)),
            "ai_citation_changes": _to_jsonable(ai_map.get(key)),
        })
    if not rows:
        return {}
    resp = sb.table(m.SCOUT_INVESTIGATIONS_TABLE).upsert(
        rows, on_conflict="run_id,trigger_key"
    ).execute()
    out: dict[tuple, str] = {}
    returned = resp.data or []
    for i, r in enumerate(returned):
        rid = r.get("id")
        if not rid:
            continue
        fallback = row_keys[i] if i < len(row_keys) else (None, "", "")
        client_id = r.get("client_id") or fallback[0]
        comp = r.get("competitor_name") or fallback[1]
        clu = r.get("cluster_id") or fallback[2]
        if client_id and comp and clu:
            out[(client_id, comp, clu)] = rid
    return out


def _write_recommendations(sb, state: ScoutState, client_ids: dict[str, str], investigation_id_map: dict[tuple, str]) -> dict[tuple, str]:
    """Upsert recommendations rows and return a {(competitor_name, cluster_id): id} map for the report writer.
    investigation_id_map (from _write_investigations) overrides the model's fabricated investigation_id with the real Supabase FK; missing entries write NULL since the column is nullable."""
    run_id = state.get("run_id", "")
    sync_date = str(state.get("sync_date", ""))
    recs = state.get("recommendations", []) or []
    rows = []
    for rec in recs:
        d = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
        client_id = d.get("client_id") or client_ids.get(d.get("client_name", "")) or None
        priority_in = d.get("priority", "")
        priority_out = _PRIORITY_MAP.get(priority_in, priority_in or "medium")
        comp = d.get("competitor_name", "")
        clu = d.get("cluster_id", "")
        investigation_id = investigation_id_map.get((client_id, comp, clu))
        rows.append({
            "run_id": run_id,
            "investigation_id": investigation_id,
            "client_id": client_id,
            "cluster_id": clu,
            "competitor_name": comp,
            "week_date": sync_date or None,
            "rec_type": d.get("type"),
            "priority": priority_out,
            "probable_cause": d.get("probable_cause"),
            "gap_analysis": d.get("gap_analysis"),
            "action_bullets": d.get("action_bullets") or [],
            "confidence": d.get("confidence"),
            "evidence_summary": d.get("summary"),
            "client_name": d.get("client_name"),
            "cluster_label": d.get("cluster_label"),
            "shift_type": d.get("shift_type"),
            "summary": d.get("summary"),
            "slack_report": d.get("slack_report"),
            "timeline": d.get("timeline"),
            "window_weeks": d.get("window_weeks"),
            "validation_status": d.get("validation_status", "ok"),
            "validation_notes": d.get("validation_notes") or [],
            "revenue_opportunity_usd": d.get("revenue_opportunity_usd"),
            "revenue_at_risk_usd": d.get("revenue_at_risk_usd"),
            "revenue_basis": d.get("revenue_basis", "none"),
            "revenue_inputs": _to_jsonable(d.get("revenue_inputs")),
        })
    if not rows:
        return {}
    resp = sb.table(m.SCOUT_RECOMMENDATIONS_TABLE).upsert(
        rows, on_conflict="run_id,client_id,cluster_id"
    ).execute()
    out: dict[tuple, str] = {}
    for r in (resp.data or []):
        key = (r.get("client_id"), r.get("competitor_name"), r.get("cluster_id"))
        if r.get("id"):
            out[key] = r["id"]
    return out


def _write_reports(sb, state: ScoutState, client_ids: dict[str, str], rec_id_map: dict[tuple, str]) -> int:
    """Upsert reports joined to recommendations by explicit client artifact identity."""
    run_id = state.get("run_id", "")
    sync_date = str(state.get("sync_date", ""))
    recs = state.get("recommendations", []) or []
    reports = state.get("internal_reports", []) or []
    summaries = state.get("client_summaries", []) or []
    report_map: dict[tuple, object] = {}
    for report in reports:
        rd = report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)
        key = (rd.get("client_id"), rd.get("competitor_name"), rd.get("cluster_id"))
        if key in report_map:
            raise ValueError(f"duplicate internal report identity: {key}")
        report_map[key] = report

    summary_map: dict[tuple, object] = {}
    legacy_summary_map: dict[tuple, object] = {}
    for summary in summaries:
        sd = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
        key = (sd.get("client_id"), sd.get("competitor_name"), sd.get("cluster_id"))
        if sd.get("competitor_name"):
            if key in summary_map:
                raise ValueError(f"duplicate client summary identity: {key}")
            summary_map[key] = summary
        else:
            legacy_key = (sd.get("client_id"), sd.get("cluster_id"))
            if legacy_key in legacy_summary_map:
                raise ValueError(f"ambiguous legacy client summary identity: {legacy_key}")
            legacy_summary_map[legacy_key] = summary

    rows = []
    for rec in recs:
        d = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
        comp = d.get("competitor_name", "")
        clu = d.get("cluster_id", "")
        client_id = d.get("client_id") or client_ids.get(d.get("client_name", "")) or None
        artifact_key = (client_id, comp, clu)
        rec_id = rec_id_map.get(artifact_key)
        if not rec_id:
            raise ValueError(f"missing recommendation ID for report identity: {artifact_key}")
        ir = report_map.get(artifact_key)
        cs = summary_map.get(artifact_key) or legacy_summary_map.get((client_id, clu))
        if ir is None or cs is None:
            raise ValueError(f"missing report artifact for recommendation identity: {artifact_key}")
        ir_text = ir.report_text if hasattr(ir, "report_text") else ir.get("report_text", "")
        cs_text = cs.summary_text if hasattr(cs, "summary_text") else cs.get("summary_text", "")
        priority_in = d.get("priority", "")
        priority_out = _PRIORITY_MAP.get(priority_in, priority_in or "medium")
        rows.append({
            "run_id": run_id,
            "recommendation_id": rec_id,
            "client_id": client_id,
            "cluster_id": clu,
            "competitor_name": comp,
            "week_date": sync_date or None,
            "internal_report": ir_text,
            "client_summary": cs_text,
            "cluster_label": d.get("cluster_label"),
            "priority": priority_out,
            "validation_status": d.get("validation_status", "ok"),
            "validation_notes": d.get("validation_notes") or [],
        })
    if not rows:
        return 0
    sb.table(m.SCOUT_REPORTS_TABLE).upsert(
        rows, on_conflict="run_id,recommendation_id"
    ).execute()
    return len(rows)


def _write_outcomes(sb, state: ScoutState, client_ids: dict[str, str], rec_id_map: dict[tuple, str]) -> int:
    """Insert one scout_outcomes row per shipped (validation_status='ok') recommendation with a ship-time SOV baseline.
    Baseline is read from in-memory sov_tracking_records NOW (snapshot) — the moving GEO mirror makes it unreconstructable later (R1-5)."""
    run_id = state.get("run_id", "")
    sync_date = str(state.get("sync_date", ""))
    recs = state.get("recommendations", []) or []
    # Index SOV baselines by the FULL (client_id, cluster_id, competitor_name) triple — cluster_id repeats
    # across clients, so a (competitor, cluster) join alone would cross-contaminate baselines between clients.
    sov_idx: dict[tuple, dict] = {}
    client_sov_idx: dict[tuple, float] = {}
    for r in state.get("sov_tracking_records", []) or []:
        d = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r)
        sov_idx[(d.get("client_id"), d.get("cluster_id"), d.get("competitor_name"))] = d
        if d.get("client_sov_this_week") is not None:
            client_sov_idx.setdefault((d.get("client_id"), d.get("cluster_id")), d.get("client_sov_this_week"))
    snapshot_at = _now_iso()
    rows = []
    for rec in recs:
        d = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
        if d.get("validation_status", "ok") != "ok":
            continue   # only shipped recommendations get an outcome row
        comp = d.get("competitor_name", "")   # == verdict.primary_competitor (recommendation_gen elects the primary)
        clu = d.get("cluster_id", "")
        client_id = d.get("client_id") or client_ids.get(d.get("client_name", "")) or None
        rec_id = rec_id_map.get((client_id, comp, clu))
        if not rec_id:
            log.warning("[sed_writer] no rec_id for (%s,%s) — skipping outcome row", comp, clu)
            continue
        sov_row = sov_idx.get((client_id, clu, comp))
        baseline_comp = sov_row.get("sov_score") if sov_row else None
        baseline_client = client_sov_idx.get((client_id, clu))
        if baseline_client is None and sov_row is not None:
            baseline_client = sov_row.get("client_sov_this_week")
        if baseline_comp is None:
            log.warning("[sed_writer] no SOV baseline for (%s,%s,%s) — outcome row written with null competitor baseline", client_id, clu, comp)
        rows.append({
            "recommendation_id": rec_id,
            "run_id": run_id,
            "client_id": client_id,
            "cluster_id": clu,
            "week_date": sync_date or None,
            "execution_status": "proposed",
            "baseline_client_sov_pp": baseline_client,
            "baseline_competitor_sov_pp": baseline_comp,
            "baseline_primary_competitor": comp,
            "baseline_snapshot_at": snapshot_at,
            "baseline_revenue_usd": (d.get("revenue_inputs") or {}).get("revenue_usd"),
            "revenue_at_risk_usd": d.get("revenue_at_risk_usd"),
            "revenue_basis": d.get("revenue_basis", "none"),
        })
    if not rows:
        return 0
    sb.table(m.SCOUT_OUTCOMES_TABLE).upsert(rows, on_conflict="recommendation_id").execute()
    return len(rows)


def _write_asset_targets(sb, state: ScoutState, client_ids: dict[str, str], rec_id_map: dict[tuple, str]) -> int:
    """Tier 2 (D4): register the target assets each shipped recommendation implies, at ship time —
    the only moment rec + in-memory client_readiness coexist (readiness is never persisted).
    Triple-flag-gated; returns 0 untouched when any gate is off (flags-off byte-identity)."""
    from scout.config import get_config
    cfg = get_config()
    if not (cfg.revenue_layer_enabled and cfg.asset_attribution_enabled and cfg.asset_target_derivation_enabled):
        return 0
    from scout.db.asset_registry import derive_target_assets
    from scout.db.asset_writer import write_scout_assets
    run_id = state.get("run_id", "")
    websites = {c.get("client_name"): (c.get("company_website") or "")
                for c in state.get("clients", []) or []}
    rows: list[dict] = []
    for rec in state.get("recommendations", []) or []:
        d = rec.model_dump(mode="json") if hasattr(rec, "model_dump") else dict(rec)
        if d.get("validation_status", "ok") != "ok":
            continue   # only shipped recommendations register assets (mirrors _write_outcomes)
        client_id = d.get("client_id") or client_ids.get(d.get("client_name", "")) or None
        rec_id = rec_id_map.get(
            (client_id, d.get("competitor_name", ""), d.get("cluster_id", ""))
        )
        if not rec_id:
            continue
        derived = derive_target_assets(
            rec, d.get("client_readiness") or {},
            recommendation_id=rec_id, run_id=run_id,
            company_website=websites.get(d.get("client_name", ""), ""),
        )
        for row in derived:
            row["client_id"] = client_id   # Supabase uuid (readiness carries the GEO id — overwrite)
        rows.extend(derived)
    if not rows:
        return 0
    # HIGH-6 (live E2E): never demote builder-owned rows — a re-run's target derivation must not
    # reset asset_source/asset_status on assets the builder already generated (lifecycle set).
    try:
        rec_ids = sorted({r.get("recommendation_id") for r in rows if r.get("recommendation_id")})
        owned = set()
        for i in range(0, len(rec_ids), 100):
            resp = (sb.table(m.SCOUT_ASSETS_TABLE).select("recommendation_id,asset_type,lifecycle_status")
                    .in_("recommendation_id", rec_ids[i:i + 100]).execute())
            for a in (resp.data or []):
                if a.get("lifecycle_status"):
                    owned.add((a.get("recommendation_id"), a.get("asset_type")))
        if owned:
            rows = [r for r in rows if (r.get("recommendation_id"), r.get("asset_type")) not in owned]
    except Exception as e:
        log.warning("[sed_writer] builder-owned filter failed (registering all): %s", e)
    return write_scout_assets(sb, rows)


def _write_blog_detections(sb, state: ScoutState, client_ids: dict[str, str]) -> int:
    """Flatten BlogDetectionResult.new_blog_posts into one blog_detections row per post and upsert.
    client_id is resolved from the first client in state when the per-post payload lacks one (DB column is NOT NULL)."""
    run_id = state.get("run_id", "")
    sync_date = str(state.get("sync_date", ""))
    detections = state.get("blog_detections", {}) or {}
    if not detections:
        return 0
    owners: dict[tuple[str, str], set[str]] = {}
    for client in state.get("clients", []) or []:
        client_id = client.get("client_id")
        if not client_id:
            continue
        for cluster in client.get("clusters", []) or []:
            cluster_id = str(cluster.get("cluster_id") or "")
            for competitor in cluster.get("competitors", []) or []:
                domain_key = str(competitor.get("competitor_domain") or "").lower()
                if domain_key and cluster_id:
                    owners.setdefault((domain_key, cluster_id), set()).add(client_id)
    rows = []
    for _domain_key, det in detections.items():
        dd = det.model_dump(mode="json") if hasattr(det, "model_dump") else dict(det)
        domain = dd.get("competitor_domain") or _domain_key
        source = dd.get("detection_method") or "feed"
        for post in dd.get("new_blog_posts", []) or []:
            url = post.get("url")
            if not url:
                continue
            cluster_relevance = ""
            relevance_reason = ""
            cluster_id = ""
            for rel in post.get("relevance_to_clusters", []) or []:
                if isinstance(rel, dict):
                    cluster_id = rel.get("cluster_id") or cluster_id
                    cluster_relevance = rel.get("relevance") or rel.get("score") or cluster_relevance
                    relevance_reason = rel.get("reason") or relevance_reason
                    break
            matching_owners = owners.get((str(domain).lower(), str(cluster_id)), set())
            if len(matching_owners) != 1:
                log.warning(
                    "[sed_writer] blog detection owner is %s for domain=%s cluster=%s; skipping",
                    "missing" if not matching_owners else "ambiguous",
                    domain,
                    cluster_id,
                )
                continue
            client_id = next(iter(matching_owners))
            rows.append({
                "run_id": run_id,
                "client_id": client_id,
                "domain": domain,
                "url": url,
                "title": post.get("title"),
                "published_at": post.get("published_date"),
                "week_date": sync_date or None,
                "content_type": post.get("content_type"),
                "cluster_id": cluster_id or None,
                "cluster_relevance": str(cluster_relevance) if cluster_relevance else None,
                "relevance_reason": relevance_reason or None,
                "source": source,
            })
    if not rows:
        return 0
    sb.table(m.SCOUT_BLOG_DETECTIONS_TABLE).upsert(
        rows, on_conflict="run_id,url"
    ).execute()
    return len(rows)


def _write_decision_log(sb, state: ScoutState, client_ids: dict[str, str]) -> int:
    """Persist one decision-log row per ClusterVerdict so 'why was this severity?' is answerable after the run.
    Captures the triage inputs (deltas), the resulting severity/priority, and the noise flag per client-cluster."""
    run_id = state.get("run_id", "")
    sync_date = str(state.get("sync_date", ""))
    verdicts = state.get("cluster_verdicts", []) or []
    from scout.config import get_config
    cfg = get_config()
    floor_set = {
        "min_floor_pp": cfg.min_floor_pp,
        "win_pp": cfg.win_pp,
        "crit_loss_pp": cfg.crit_loss_pp,
        "established_pp": cfg.established_pp,
    }
    floor_version = cfg.severity_floor_version
    rows = []
    for v in verdicts:
        d = v.model_dump(mode="json") if hasattr(v, "model_dump") else dict(v)
        primary = d.get("primary_competitor", "")
        primary_delta = next(
            (f.get("delta_pp") for f in (d.get("field") or []) if f.get("competitor") == primary), None
        )
        rows.append({
            "run_id": run_id,
            "client_id": d.get("client_id") or client_ids.get(d.get("client_name", "")) or None,
            "cluster_id": d.get("cluster_id"),
            "cluster_label": d.get("cluster_label"),
            "week_date": sync_date or None,
            "primary_competitor": primary,
            "primary_delta_pp": primary_delta,
            "delta_client_pp": d.get("delta_client"),
            "triage_severity": d.get("triage_severity"),
            "investigation_priority": d.get("investigation_priority"),
            "noise": bool(d.get("noise", False)),
            "field": _to_jsonable(d.get("field")),
            "floor_set": floor_set,
            "severity_floor_version": floor_version,
            "graduation_regime": d.get("graduation_regime"),   # R4-3: news_mode|graduated
        })
    if not rows:
        return 0
    sb.table(m.SCOUT_DECISION_LOG_TABLE).upsert(
        rows, on_conflict="run_id,client_id,cluster_id"
    ).execute()
    return len(rows)


def write_outputs_to_sed(state: ScoutState) -> dict[str, dict]:
    """Persist every pipeline artifact for this run into the user's Supabase project.
    Each helper is isolated so optional writes can continue, while every failure
    remains visible to the lifecycle owner."""
    from scout.db.supabase_client import get_sed_client
    sb = get_sed_client()
    client_ids = _client_id_lookup(state)
    trigger_meta = _trigger_lookup(state)
    counts: dict[str, int] = {}
    failures: dict[str, str] = {}

    for label, fn in [
        ("sov_tracking", lambda: _write_sov_tracking(sb, state)),
    ]:
        try:
            counts[label] = fn()
        except Exception as e:
            log.warning("[sed_writer] %s write failed: %s", label, e)
            counts[label] = 0
            failures[label] = str(e)

    try:
        trigger_id_map = _write_investigation_triggers(sb, state)
        counts["investigation_triggers"] = len(trigger_id_map)
    except Exception as e:
        log.warning("[sed_writer] investigation_triggers write failed: %s", e)
        trigger_id_map = {}
        counts["investigation_triggers"] = 0
        failures["investigation_triggers"] = str(e)

    try:
        investigation_id_map = _write_investigations(sb, state, trigger_id_map, trigger_meta)
        counts["investigations"] = len(investigation_id_map)
    except Exception as e:
        log.warning("[sed_writer] investigations write failed: %s", e)
        investigation_id_map = {}
        counts["investigations"] = 0
        failures["investigations"] = str(e)

    try:
        rec_id_map = _write_recommendations(sb, state, client_ids, investigation_id_map)
        counts["recommendations"] = len(rec_id_map)
    except Exception as e:
        log.warning("[sed_writer] recommendations write failed: %s", e)
        rec_id_map = {}
        counts["recommendations"] = 0
        failures["recommendations"] = str(e)

    try:
        counts["reports"] = _write_reports(sb, state, client_ids, rec_id_map)
    except Exception as e:
        log.warning("[sed_writer] reports write failed: %s", e)
        counts["reports"] = 0
        failures["reports"] = str(e)

    try:
        counts["outcomes"] = _write_outcomes(sb, state, client_ids, rec_id_map)
    except Exception as e:
        log.warning("[sed_writer] outcomes write failed: %s", e)
        counts["outcomes"] = 0
        failures["outcomes"] = str(e)

    try:
        counts["scout_assets"] = _write_asset_targets(sb, state, client_ids, rec_id_map)
    except Exception as e:
        log.warning("[sed_writer] scout_assets write failed: %s", e)
        counts["scout_assets"] = 0
        failures["scout_assets"] = str(e)

    try:
        counts["blog_detections"] = _write_blog_detections(sb, state, client_ids)
    except Exception as e:
        log.warning("[sed_writer] blog_detections write failed: %s", e)
        counts["blog_detections"] = 0
        failures["blog_detections"] = str(e)

    try:
        counts["decision_log"] = _write_decision_log(sb, state, client_ids)
    except Exception as e:
        log.warning("[sed_writer] decision_log write failed: %s", e)
        counts["decision_log"] = 0
        failures["decision_log"] = str(e)

    log.info("[sed_writer] write counts: %s", json.dumps(counts))
    return {"counts": counts, "failures": failures}
