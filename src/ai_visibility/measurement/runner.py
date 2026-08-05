"""Orchestrate measurement: plan first, evaluate later.

The two steps are deliberately separate. `plan_measurements` runs as soon as an execution is
recorded — before anyone can know how it turned out. `evaluate_plans` fills in the numbers once the
window has closed. Choosing a window after seeing the result is the easiest way for a measurement
system to flatter itself, and separating these removes the opportunity rather than relying on
discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.measurement import aggregation as A
from ai_visibility.measurement import (
    ga4,
    gsc,
    mapping,
    outcomes,
    persistence,
    tenancy,
    windows,
)


@dataclass(frozen=True)
class PlanReport:
    subject_id: str
    status: str
    reason: str
    matched_queries: int = 0
    matched_pages: int = 0


@dataclass(frozen=True)
class EvaluationReport:
    subject_id: str
    classification: str
    confidence: str
    deltas: list[dict[str, Any]]


def _load_scope(
    settings: Settings, client_id: UUID, owned_domains: frozenset[str]
) -> tenancy.TenantScope:
    """Resolve which sources may be read for this client, from live handle ownership."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = [
            dict(row)
            for row in cursor.execute(
                "select client_id, gsc_site_url, ga4_property_id from public.user_metrics"
            ).fetchall()
        ]
    owners = tenancy.build_handle_owners(rows, excluded_client_ids=settings.excluded_client_ids)
    mine = next((r for r in rows if str(r["client_id"]) == str(client_id)), None)
    return tenancy.resolve_tenant_scope(
        client_id,
        handles=(
            {"gsc_site_url": mine["gsc_site_url"], "ga4_property_id": mine["ga4_property_id"]}
            if mine
            else None
        ),
        owned_domains=owned_domains,
        handle_owners=owners,
        excluded_client_ids=settings.excluded_client_ids,
        shared_handle_overrides=settings.shared_handle_overrides,
    )


def _executed_subjects(settings: Settings, client_id: UUID) -> list[dict[str, Any]]:
    """Executions with a real anchor, joined to whatever targets they froze.

    Falls back to the recommendation's own targets only when the execution recorded none — what was
    measured should be what shipped."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(
            """
            select execution.subject_id,
                   execution.subject_type,
                   execution.implemented_at,
                   case when jsonb_array_length(execution.target_pages) > 0
                        then execution.target_pages else coalesce(rec.target_pages, '[]'::jsonb)
                   end as target_pages,
                   case when jsonb_array_length(execution.target_queries) > 0
                        then execution.target_queries
                        else coalesce(rec.target_queries, '[]'::jsonb)
                   end as target_queries
              from public.aivc_action_executions execution
              left join public.recommendations rec
                     on rec.id::text = execution.subject_id
             where execution.client_id = %s
               and execution.status in ('executed', 'verified')
               and execution.implemented_at is not null
            """,
            (client_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _plan_status(
    scope: tenancy.TenantScope, mapped: mapping.MappingResult, window: windows.MeasurementWindow
) -> str:
    """Grade a plan by what actually stops it being measurable, most fundamental first."""
    if not scope.gsc_measurable or not mapped.has_any_target:
        return "unmeasurable"
    if window.status == windows.WAITING_FOR_WINDOW:
        return "waiting_for_window"
    return "ready"


def _plan_one(
    settings: Settings, subject: dict[str, Any], *, client_id: UUID,
    scope: tenancy.TenantScope, today: date, source_fresh_through: date | None,
) -> PlanReport:
    """Plan a single executed recommendation."""
    anchor_at: datetime = subject["implemented_at"]
    window = windows.build_window(
        anchor_at.date(), today=today,
        window_days=settings.measurement_window_days,
        source_lag_days=settings.measurement_source_lag_days,
        source_fresh_through=source_fresh_through,
    )
    if window.baseline_start is None or window.follow_up_end is None:
        return PlanReport(subject["subject_id"], "unmeasurable", window.reason)

    target_pages = list(subject["target_pages"] or [])
    target_queries = list(subject["target_queries"] or [])
    vocabulary: tuple[list[str], list[str]] = ([], [])
    if scope.gsc_measurable:
        vocabulary = gsc.fetch_source_vocabulary(
            settings, scope, start=window.baseline_start, end=window.follow_up_end
        )
    mapped = mapping.map_targets(
        target_queries=target_queries, target_pages=target_pages,
        source_queries=vocabulary[0], source_pages=vocabulary[1],
        owned_page_check=gsc.owned_page_checker(scope),
    )
    status = _plan_status(scope, mapped, window)

    persistence.upsert_plan(
        settings,
        subject_type=subject["subject_type"],
        subject_id=subject["subject_id"],
        client_id=client_id,
        anchor_at=anchor_at,
        baseline_start=window.baseline_start,
        baseline_end=window.baseline_end,           # type: ignore[arg-type]
        follow_up_start=window.follow_up_start,     # type: ignore[arg-type]
        follow_up_end=window.follow_up_end,
        window_days=window.window_days,
        target_pages=target_pages,
        target_queries=target_queries,
        mapping_details={**mapped.as_dict(), "tenancy_warnings": scope.warnings},
        status=status,
    )
    reason = window.reason
    if status == "unmeasurable":
        reason = f"gsc={scope.gsc_status}; no target matched the source"
    return PlanReport(
        subject["subject_id"], status, reason,
        len(mapped.matched_queries), len(mapped.matched_pages),
    )


def _capture_ga4(
    settings: Settings, scope: tenancy.TenantScope,
    plan: persistence.StoredPlan, window: windows.MeasurementWindow,
) -> list[dict[str, Any]]:
    """Capture the GA4 engagement layer for a plan, and persist both window snapshots.

    Returns [] when GA4 is unavailable for this client — refused as a shared property, not
    connected, or with no target page that GA4 holds. An empty list is read downstream as
    `unavailable`, never as zero engagement."""
    if not scope.ga4_measurable or not plan.target_pages:
        return []

    source_pages = ga4.fetch_source_pages(
        settings, scope, start=plan.baseline_start, end=plan.follow_up_end
    )
    mapped = mapping.map_targets(
        target_queries=[], target_pages=plan.target_pages,
        source_queries=[], source_pages=source_pages,
        owned_page_check=ga4.owned_page_checker(scope),
    )
    if not mapped.matched_pages:
        return []

    totals = {}
    for window_type, start, end in (
        ("baseline", plan.baseline_start, plan.baseline_end),
        ("follow_up", plan.follow_up_start, plan.follow_up_end),
    ):
        fetched = ga4.fetch_window(
            settings, scope, start=start, end=end, matched_pages=mapped.matched_pages
        )
        aggregated = A.aggregate_ga4_rows(fetched.rows)
        totals[window_type] = aggregated
        persistence.upsert_snapshot(
            settings, plan_id=plan.id, source="ga4", window_type=window_type,
            requested_start=start, requested_end=end,
            effective_start=fetched.effective_start, effective_end=fetched.effective_end,
            fresh_through=window.fresh_through, row_count=aggregated.row_count,
            metrics=aggregated.as_dict(), source_status=fetched.source_status,
            warnings=fetched.warnings,
        )
    return A.compare_ga4_windows(totals["baseline"], totals["follow_up"])


def plan_measurements(
    settings: Settings,
    *,
    client_id: UUID,
    owned_domains: frozenset[str],
    today: date | None = None,
) -> list[PlanReport]:
    """Create or refresh a measurement plan for every executed recommendation."""
    today = today or date.today()
    scope = _load_scope(settings, client_id, owned_domains)
    fresh_through = gsc.fetch_fresh_through(settings, scope)
    return [
        _plan_one(
            settings, subject, client_id=client_id, scope=scope, today=today,
            source_fresh_through=fresh_through,
        )
        for subject in _executed_subjects(settings, client_id)
    ]


def evaluate_plans(
    settings: Settings,
    *,
    client_id: UUID,
    owned_domains: frozenset[str],
    today: date | None = None,
) -> list[EvaluationReport]:
    """Capture both windows and record an outcome for every plan whose window has closed."""
    today = today or date.today()
    scope = _load_scope(settings, client_id, owned_domains)
    source_fresh_through = gsc.fetch_fresh_through(settings, scope)
    results: list[EvaluationReport] = []

    for plan in persistence.load_plans(settings, client_id=client_id):
        window = windows.build_window(
            plan.anchor_at.date(),
            today=today,
            window_days=plan.window_days,
            source_lag_days=settings.measurement_source_lag_days,
            source_fresh_through=source_fresh_through,
        )
        vocabulary: tuple[list[str], list[str]] = ([], [])
        if scope.gsc_measurable:
            vocabulary = gsc.fetch_source_vocabulary(
                settings, scope, start=plan.baseline_start, end=plan.follow_up_end
            )
        details = mapping.map_targets(
            target_queries=plan.target_queries,
            target_pages=plan.target_pages,
            source_queries=vocabulary[0],
            source_pages=vocabulary[1],
            owned_page_check=gsc.owned_page_checker(scope),
        )

        captured = {}
        for window_type, start, end in (
            ("baseline", plan.baseline_start, plan.baseline_end),
            ("follow_up", plan.follow_up_start, plan.follow_up_end),
        ):
            fetched = gsc.fetch_window(
                settings, scope, start=start, end=end,
                matched_queries=details.matched_queries, matched_pages=details.matched_pages,
            )
            totals = A.aggregate_gsc_rows(fetched.rows)
            captured[window_type] = (fetched, totals)
            persistence.upsert_snapshot(
                settings, plan_id=plan.id, source="gsc", window_type=window_type,
                requested_start=start, requested_end=end,
                effective_start=fetched.effective_start, effective_end=fetched.effective_end,
                fresh_through=window.fresh_through, row_count=totals.row_count,
                metrics=totals.as_dict(), source_status=fetched.source_status,
                warnings=fetched.warnings,
            )

        baseline_fetch, baseline_totals = captured["baseline"]
        _, follow_up_totals = captured["follow_up"]
        deltas = A.compare_windows(baseline_totals, follow_up_totals)
        outcome = outcomes.classify_gsc(
            deltas,
            baseline_impressions=baseline_totals.impressions,
            window_status=window.status,
            source_status=baseline_fetch.source_status,
            warnings=baseline_fetch.warnings,
        )
        ga4_deltas = _capture_ga4(settings, scope, plan, window)
        persistence.upsert_outcome(
            settings, plan_id=plan.id,
            classification=outcome.classification, confidence=outcome.confidence,
            gsc_deltas=outcome.deltas, ga4_deltas=ga4_deltas,
            evidence_summary=outcome.evidence,
            limitations=outcome.limitations, warnings=outcome.warnings,
            algorithm_version=outcome.algorithm_version,
        )
        results.append(
            EvaluationReport(
                plan.subject_id, outcome.classification, outcome.confidence, outcome.deltas
            )
        )
    return results
