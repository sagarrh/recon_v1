from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from aivc.config.settings import AivcSettings
from aivc.database.measurement import audit_measurement_foundation
from aivc.measurement.aggregation import make_snapshot
from aivc.measurement.evaluation import evaluate_snapshots
from aivc.measurement.models import (
    MeasurementOutcome,
    SourceName,
    SourceSnapshot,
    StartMeasurementRequest,
    StartMeasurementResult,
    WindowType,
)
from aivc.measurement.planning import (
    measurement_windows,
    normalized_targets,
    plan_idempotency_key,
    required_sources,
)
from aivc.measurement.targets import normalized_page_targets, normalized_queries


def _verify_subject(cursor: Any, request: StartMeasurementRequest) -> None:
    table = (
        "public.recommendations"
        if request.subject_type.value == "recon_recommendation"
        else "public.ai_visibility_signals"
    )
    row = cursor.execute(
        f"select id from {table} where id::text = %s and client_id = %s",
        (request.subject_id, request.client_id),
    ).fetchone()
    if row is None:
        raise LookupError(
            f"{request.subject_type.value} '{request.subject_id}' was not found for this client."
        )


def start_measurement(
    settings: Settings,
    aivc_settings: AivcSettings,
    request: StartMeasurementRequest,
) -> StartMeasurementResult:
    """Register a verified implementation and its idempotent measurement plan."""
    health = audit_measurement_foundation(
        settings,
        client_id=request.client_id,
        gsc_max_lag_days=aivc_settings.aivc_measurement_gsc_max_lag_days,
        ga4_max_lag_days=aivc_settings.aivc_measurement_ga4_max_lag_days,
    )
    if not health.ready_for_gsc_measurement:
        raise RuntimeError("GSC measurement foundation is not ready: " + ", ".join(health.warnings))
    if request.require_ga4 and not health.ready_for_ga4_measurement:
        raise RuntimeError(
            "GA4 is required for this plan but is not ready: " + ", ".join(health.warnings)
        )
    pages, queries = normalized_targets(request)
    windows = measurement_windows(request)
    sources = required_sources(request)
    idempotency_key = plan_idempotency_key(request)
    mapping_details = {
        "match_policy": "exact_intersection",
        "normalized_target_pages": pages,
        "normalized_target_queries": queries,
        "property_wide_ga4_allowed": False,
        "currency_code": health.currency_code,
        "mapping_version": "exact_v1",
    }
    lock_key = (
        f"measurement:{request.client_id}:{request.subject_type.value}:"
        f"{request.subject_id}:{request.implemented_at.isoformat()}"
    )
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
        _verify_subject(cursor, request)
        action = cursor.execute(
            """
            select id from public.aivc_action_executions
            where client_id = %s and subject_type = %s and subject_id = %s
              and implemented_at = %s
            order by created_at
            limit 1
            """,
            (
                request.client_id,
                request.subject_type.value,
                request.subject_id,
                request.implemented_at,
            ),
        ).fetchone()
        if action is None:
            action = cursor.execute(
                """
                insert into public.aivc_action_executions(
                  subject_type, subject_id, client_id, status, implemented_at,
                  implemented_by, target_pages, target_queries, action_type,
                  implementation_notes, evidence_urls, verification_status,
                  verified_at
                ) values (
                  %s, %s, %s, 'verified', %s, %s, %s, %s, %s, %s, %s, %s, now()
                )
                returning id
                """,
                (
                    request.subject_type.value,
                    request.subject_id,
                    request.client_id,
                    request.implemented_at,
                    request.implemented_by,
                    Jsonb(pages),
                    Jsonb(queries),
                    request.action_type,
                    request.implementation_notes,
                    Jsonb(request.evidence_urls),
                    request.verification_status.value,
                ),
            ).fetchone()
        if action is None:
            raise RuntimeError("Action execution insert returned no ID.")
        mapping_details["action_execution_id"] = str(action["id"])
        plan = cursor.execute(
            """
            insert into public.ai_visibility_measurement_plans(
              idempotency_key, subject_type, subject_id, client_id, anchor_at,
              baseline_start, baseline_end, follow_up_start, follow_up_end,
              window_days, target_pages, target_queries, mapping_details,
              required_sources, status, policy_version
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, 'waiting_for_window', 'measurement_v1'
            )
            on conflict(idempotency_key) do update set
              mapping_details = excluded.mapping_details,
              updated_at = now()
            returning id, status
            """,
            (
                idempotency_key,
                request.subject_type.value,
                request.subject_id,
                request.client_id,
                request.implemented_at,
                windows.baseline_start,
                windows.baseline_end,
                windows.follow_up_start,
                windows.follow_up_end,
                request.follow_up_days,
                Jsonb(pages),
                Jsonb(queries),
                Jsonb(mapping_details),
                [source.value for source in sources],
            ),
        ).fetchone()
        if plan is None:
            raise RuntimeError("Measurement plan insert returned no ID.")
        connection.commit()
    return StartMeasurementResult(
        action_execution_id=UUID(str(action["id"])),
        plan_id=UUID(str(plan["id"])),
        plan_status=str(plan["status"]),
        windows=windows,
        required_sources=sources,
    )


def _load_plan(cursor: Any, plan_id: UUID) -> dict[str, Any]:
    row = cursor.execute(
        """
        select p.*,
               um.gsc_site_url, um.ga4_property_id,
               nullif(upper(btrim(o.currency)), '') as currency_code
        from public.ai_visibility_measurement_plans p
        left join public.user_metrics um on um.client_id = p.client_id
        left join public.onboarding o on o.client_id = p.client_id
        where p.id = %s
        """,
        (plan_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Measurement plan '{plan_id}' was not found.")
    return dict(row)


def _source_rows(
    cursor: Any,
    *,
    source: SourceName,
    handle: str,
    start: date,
    end: date,
    maximum_rows: int,
) -> tuple[list[dict[str, Any]], date | None]:
    if source is SourceName.gsc:
        rows = cursor.execute(
            """
            select metric_date, query, page, clicks, impressions, ctr, position,
                   country, device
            from public.gsc_query_page_metrics
            where site_url = %s and metric_date between %s and %s
            order by metric_date, query, page, country, device
            limit %s
            """,
            (handle, start, end, maximum_rows + 1),
        ).fetchall()
        fresh = cursor.execute(
            """
            select max(metric_date) as latest
            from public.gsc_query_page_metrics where site_url = %s
            """,
            (handle,),
        ).fetchone()
    else:
        rows = cursor.execute(
            """
            select metric_date, source, medium, campaign, landing_page, country,
                   device, sessions, engaged_sessions, conversions, revenue
            from public.ga4_metrics
            where property_id = %s and metric_date between %s and %s
            order by metric_date, landing_page, source, medium, campaign, country, device
            limit %s
            """,
            (handle, start, end, maximum_rows + 1),
        ).fetchall()
        fresh = cursor.execute(
            "select max(metric_date) as latest from public.ga4_metrics where property_id = %s",
            (handle,),
        ).fetchone()
    if len(rows) > maximum_rows:
        raise RuntimeError(
            f"{source.value.upper()} measurement exceeded the safe row limit of {maximum_rows}."
        )
    return [dict(row) for row in rows], fresh["latest"] if fresh else None


def _assert_unshared_handles(cursor: Any, plan: dict[str, Any]) -> None:
    gsc_handle = str(plan.get("gsc_site_url") or "").strip() or None
    ga4_handle = str(plan.get("ga4_property_id") or "").strip() or None
    row = cursor.execute(
        """
        select
          count(*) filter (where gsc_site_url = %s) as gsc_clients,
          count(*) filter (where ga4_property_id = %s) as ga4_clients
        from public.user_metrics
        """,
        (gsc_handle, ga4_handle),
    ).fetchone()
    if row is None:
        raise RuntimeError("Client measurement handle validation returned no result.")
    if gsc_handle and int(row["gsc_clients"] or 0) != 1:
        raise RuntimeError("GSC handle is no longer uniquely mapped to this client.")
    if ga4_handle and int(row["ga4_clients"] or 0) != 1:
        raise RuntimeError("GA4 handle is no longer uniquely mapped to this client.")


def _persist_snapshot(cursor: Any, plan_id: UUID, snapshot: SourceSnapshot) -> UUID:
    existing = cursor.execute(
        """
        select id from public.ai_visibility_measurement_snapshots
        where plan_id = %s and source = %s and window_type = %s
          and payload_checksum = %s
        order by captured_at desc limit 1
        """,
        (
            plan_id,
            snapshot.source.value,
            snapshot.window_type.value,
            snapshot.payload_checksum,
        ),
    ).fetchone()
    if existing is not None:
        return UUID(str(existing["id"]))
    row = cursor.execute(
        """
        insert into public.ai_visibility_measurement_snapshots(
          plan_id, source, window_type, requested_start, requested_end,
          effective_start, effective_end, fresh_through, row_count, metrics,
          source_status, warnings, payload_checksum
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            plan_id,
            snapshot.source.value,
            snapshot.window_type.value,
            snapshot.requested_start,
            snapshot.requested_end,
            snapshot.effective_start,
            snapshot.effective_end,
            snapshot.fresh_through,
            snapshot.row_count,
            Jsonb(snapshot.metrics),
            snapshot.source_status,
            Jsonb(snapshot.warnings),
            snapshot.payload_checksum,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("Measurement snapshot insert returned no ID.")
    return UUID(str(row["id"]))


def _latest_snapshots(cursor: Any, plan_id: UUID) -> list[SourceSnapshot]:
    rows = cursor.execute(
        """
        select distinct on (source, window_type)
               source, window_type, requested_start, requested_end,
               effective_start, effective_end, fresh_through, row_count,
               metrics, source_status, warnings, payload_checksum
        from public.ai_visibility_measurement_snapshots
        where plan_id = %s
        order by source, window_type, captured_at desc
        """,
        (plan_id,),
    ).fetchall()
    return [SourceSnapshot.model_validate(dict(row)) for row in rows]


def _plan_capture_status(plan: dict[str, Any], snapshots: list[SourceSnapshot]) -> str:
    indexed = {(item.source.value, item.window_type.value): item for item in snapshots}
    terminal_failure = False
    waiting = False
    for source in plan.get("required_sources") or ["gsc"]:
        for window in ("baseline", "follow_up"):
            snapshot = indexed.get((str(source), window))
            if snapshot is None or snapshot.source_status == "stale":
                waiting = True
            elif snapshot.source_status in ("not_connected", "unmapped", "refused"):
                terminal_failure = True
    if terminal_failure:
        return "unmeasurable"
    if waiting:
        return "waiting_for_window"
    return "ready"


def capture_measurement_plan(
    settings: Settings,
    aivc_settings: AivcSettings,
    *,
    plan_id: UUID,
    as_of: date | None = None,
) -> dict[str, Any]:
    observed_on = as_of or date.today()
    with connect(settings) as connection, connection.cursor() as cursor:
        plan = _load_plan(cursor, plan_id)
        _assert_unshared_handles(cursor, plan)
        pages = [str(value) for value in plan.get("target_pages") or []]
        queries = [str(value) for value in plan.get("target_queries") or []]
        captures: list[dict[str, Any]] = []
        windows = {
            WindowType.baseline: (plan["baseline_start"], plan["baseline_end"]),
            WindowType.follow_up: (plan["follow_up_start"], plan["follow_up_end"]),
        }
        for source in SourceName:
            handle_key = "gsc_site_url" if source is SourceName.gsc else "ga4_property_id"
            handle = str(plan.get(handle_key) or "").strip()
            lag_days = (
                aivc_settings.aivc_measurement_gsc_max_lag_days
                if source is SourceName.gsc
                else aivc_settings.aivc_measurement_ga4_max_lag_days
            )
            for window_type, (start, end) in windows.items():
                if observed_on < end + timedelta(days=lag_days):
                    continue
                rows: list[dict[str, Any]] = []
                fresh_through = None
                if handle:
                    rows, fresh_through = _source_rows(
                        cursor,
                        source=source,
                        handle=handle,
                        start=start,
                        end=end,
                        maximum_rows=aivc_settings.aivc_measurement_max_source_rows,
                    )
                snapshot = make_snapshot(
                    source=source,
                    window_type=window_type,
                    requested_start=start,
                    requested_end=end,
                    rows=rows,
                    target_pages=pages,
                    target_queries=queries,
                    connected=bool(handle),
                    fresh_through=fresh_through,
                )
                snapshot_id = _persist_snapshot(cursor, plan_id, snapshot)
                captures.append(
                    {
                        "snapshot_id": snapshot_id,
                        "source": source.value,
                        "window_type": window_type.value,
                        "status": snapshot.source_status,
                        "row_count": snapshot.row_count,
                    }
                )
        snapshots = _latest_snapshots(cursor, plan_id)
        status = _plan_capture_status(plan, snapshots)
        cursor.execute(
            """
            update public.ai_visibility_measurement_plans
            set status = %s, updated_at = now()
            where id = %s
            """,
            (status, plan_id),
        )
        connection.commit()
    return {"plan_id": plan_id, "status": status, "captures": captures}


def _overlapping_actions(cursor: Any, plan: dict[str, Any]) -> bool:
    details = plan.get("mapping_details") or {}
    current_action_id = str(details.get("action_execution_id") or "")
    rows = cursor.execute(
        """
        select id, target_pages, target_queries
        from public.aivc_action_executions
        where client_id = %s
          and status in ('executed', 'verified')
          and implemented_at::date between %s and %s
          and id::text <> %s
        """,
        (
            plan["client_id"],
            plan["baseline_start"],
            plan["follow_up_end"],
            current_action_id,
        ),
    ).fetchall()
    plan_pages = set(normalized_page_targets([str(value) for value in plan["target_pages"] or []]))
    plan_queries = normalized_queries([str(value) for value in plan["target_queries"] or []])
    for row in rows:
        other_pages = set(
            normalized_page_targets([str(value) for value in row.get("target_pages") or []])
        )
        other_queries = normalized_queries(
            [str(value) for value in row.get("target_queries") or []]
        )
        if plan_pages.intersection(other_pages) or plan_queries.intersection(other_queries):
            return True
    return False


def evaluate_measurement_plan(settings: Settings, *, plan_id: UUID) -> MeasurementOutcome:
    with connect(settings) as connection, connection.cursor() as cursor:
        plan = _load_plan(cursor, plan_id)
        snapshots = _latest_snapshots(cursor, plan_id)
        outcome = evaluate_snapshots(
            plan_id=plan_id,
            snapshots=snapshots,
            currency_code=plan.get("currency_code"),
            overlapping_actions=_overlapping_actions(cursor, plan),
        )
        cursor.execute(
            """
            insert into public.ai_visibility_measurement_outcomes(
              plan_id, classification, confidence, gsc_deltas, ga4_deltas,
              revenue_category, recorded_revenue, observed_revenue_delta,
              currency_code, evidence_summary, limitations, warnings,
              algorithm_version, evaluated_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      'outcome_v1', now())
            on conflict(plan_id) do update set
              classification = excluded.classification,
              confidence = excluded.confidence,
              gsc_deltas = excluded.gsc_deltas,
              ga4_deltas = excluded.ga4_deltas,
              revenue_category = excluded.revenue_category,
              recorded_revenue = excluded.recorded_revenue,
              observed_revenue_delta = excluded.observed_revenue_delta,
              currency_code = excluded.currency_code,
              evidence_summary = excluded.evidence_summary,
              limitations = excluded.limitations,
              warnings = excluded.warnings,
              algorithm_version = excluded.algorithm_version,
              evaluated_at = excluded.evaluated_at
            """,
            (
                plan_id,
                outcome.classification,
                outcome.confidence,
                Jsonb(outcome.gsc_deltas),
                Jsonb(outcome.ga4_deltas),
                outcome.revenue_category,
                outcome.recorded_revenue,
                outcome.observed_revenue_delta,
                outcome.currency_code,
                Jsonb(outcome.evidence_summary),
                Jsonb(outcome.limitations),
                Jsonb(outcome.warnings),
            ),
        )
        status = "measured" if outcome.confidence != "none" else "unmeasurable"
        cursor.execute(
            """
            update public.ai_visibility_measurement_plans
            set status = %s, updated_at = now() where id = %s
            """,
            (status, plan_id),
        )
        connection.commit()
    return outcome


def run_measurement_plan(
    settings: Settings,
    aivc_settings: AivcSettings,
    *,
    plan_id: UUID,
    as_of: date | None = None,
) -> dict[str, Any]:
    capture = capture_measurement_plan(
        settings,
        aivc_settings,
        plan_id=plan_id,
        as_of=as_of,
    )
    if capture["status"] != "ready":
        return {**capture, "outcome": None}
    outcome = evaluate_measurement_plan(settings, plan_id=plan_id)
    return {**capture, "status": "measured", "outcome": outcome.model_dump(mode="json")}


def measurement_plan_status(settings: Settings, *, plan_id: UUID) -> dict[str, Any]:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        plan = _load_plan(cursor, plan_id)
        snapshots = _latest_snapshots(cursor, plan_id)
        outcome = cursor.execute(
            """
            select classification, confidence, gsc_deltas, ga4_deltas,
                   revenue_category, recorded_revenue, observed_revenue_delta,
                   currency_code, evidence_summary, limitations, warnings,
                   algorithm_version, evaluated_at
            from public.ai_visibility_measurement_outcomes
            where plan_id = %s
            """,
            (plan_id,),
        ).fetchone()
    return {
        "plan_id": plan_id,
        "client_id": plan["client_id"],
        "subject_type": plan["subject_type"],
        "subject_id": plan["subject_id"],
        "status": plan["status"],
        "anchor_at": plan["anchor_at"],
        "baseline": {"start": plan["baseline_start"], "end": plan["baseline_end"]},
        "follow_up": {"start": plan["follow_up_start"], "end": plan["follow_up_end"]},
        "target_pages": plan["target_pages"],
        "target_queries": plan["target_queries"],
        "required_sources": plan["required_sources"],
        "snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots],
        "outcome": dict(outcome) if outcome else None,
    }
