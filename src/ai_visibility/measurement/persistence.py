"""Idempotent writes for measurement plans, snapshots and outcomes.

Every write here is keyed so that re-running is a correction, never a duplicate. That matters more
than usual: a measurement system that accumulates near-identical rows lets someone pick the run
whose numbers they prefer, which quietly destroys the point of measuring at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.utils.hashing import stable_json_hash

POLICY_VERSION = "measurement_v1"


@dataclass(frozen=True)
class StoredPlan:
    id: UUID
    subject_type: str
    subject_id: str
    client_id: UUID
    anchor_at: datetime
    baseline_start: date
    baseline_end: date
    follow_up_start: date
    follow_up_end: date
    window_days: int
    target_pages: list[str]
    target_queries: list[str]
    status: str


def plan_idempotency_key(
    *,
    client_id: UUID,
    subject_type: str,
    subject_id: str,
    anchor_at: datetime,
    target_pages: list[str],
    target_queries: list[str],
    window_days: int,
) -> str:
    """Derived only from what is known at plan time.

    Deliberately excludes anything observed later, so a plan cannot be silently re-keyed once its
    result is known — the same intent always resolves to the same row."""
    return stable_json_hash(
        {
            "client_id": str(client_id),
            "subject_type": subject_type,
            "subject_id": subject_id,
            "anchor_at": anchor_at.isoformat(),
            "target_pages": sorted(target_pages),
            "target_queries": sorted(target_queries),
            "window_days": window_days,
            "policy_version": POLICY_VERSION,
        }
    )


def _to_plan(row: dict[str, Any]) -> StoredPlan:
    return StoredPlan(
        id=UUID(str(row["id"])),
        subject_type=str(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        client_id=UUID(str(row["client_id"])),
        anchor_at=row["anchor_at"],
        baseline_start=row["baseline_start"],
        baseline_end=row["baseline_end"],
        follow_up_start=row["follow_up_start"],
        follow_up_end=row["follow_up_end"],
        window_days=int(row["window_days"]),
        target_pages=list(row["target_pages"] or []),
        target_queries=list(row["target_queries"] or []),
        status=str(row["status"]),
    )


def upsert_plan(
    settings: Settings,
    *,
    subject_type: str,
    subject_id: str,
    client_id: UUID,
    anchor_at: datetime,
    baseline_start: date,
    baseline_end: date,
    follow_up_start: date,
    follow_up_end: date,
    window_days: int,
    target_pages: list[str],
    target_queries: list[str],
    mapping_details: dict[str, Any],
    status: str,
    required_sources: list[str] | None = None,
) -> StoredPlan:
    """Create or refresh the plan for one subject. Written BEFORE the follow-up window closes."""
    key = plan_idempotency_key(
        client_id=client_id, subject_type=subject_type, subject_id=subject_id,
        anchor_at=anchor_at, target_pages=target_pages, target_queries=target_queries,
        window_days=window_days,
    )
    with connect(settings) as connection, connection.cursor() as cursor:
        row = cursor.execute(
            """
            insert into public.ai_visibility_measurement_plans(
              idempotency_key, subject_type, subject_id, client_id, anchor_at,
              baseline_start, baseline_end, follow_up_start, follow_up_end, window_days,
              target_pages, target_queries, mapping_details, required_sources, status,
              policy_version
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (idempotency_key) do update set
              mapping_details = excluded.mapping_details,
              status = excluded.status,
              updated_at = now()
            returning *
            """,
            (
                key, subject_type, subject_id, client_id, anchor_at,
                baseline_start, baseline_end, follow_up_start, follow_up_end, window_days,
                Jsonb(target_pages), Jsonb(target_queries), Jsonb(mapping_details),
                required_sources or ["gsc"], status, POLICY_VERSION,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Measurement plan upsert returned no row.")
        connection.commit()
    return _to_plan(row)


def upsert_snapshot(
    settings: Settings,
    *,
    plan_id: UUID,
    source: str,
    window_type: str,
    requested_start: date,
    requested_end: date,
    effective_start: date | None,
    effective_end: date | None,
    fresh_through: date | None,
    row_count: int,
    metrics: dict[str, Any],
    source_status: str,
    warnings: list[str],
) -> str:
    """Store one window's aggregate. Returns the payload checksum.

    Keyed on (plan, source, window) so re-capturing overwrites rather than accumulating — but the
    checksum changes if the underlying data changed, which is how a late-arriving backfill becomes
    visible instead of silently altering a published result."""
    checksum = stable_json_hash(
        {"metrics": metrics, "row_count": row_count, "source_status": source_status,
         "effective_start": str(effective_start), "effective_end": str(effective_end)}
    )
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            insert into public.ai_visibility_measurement_snapshots(
              plan_id, source, window_type, requested_start, requested_end,
              effective_start, effective_end, fresh_through, row_count, metrics,
              source_status, warnings, payload_checksum
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (plan_id, source, window_type) do update set
              requested_start = excluded.requested_start,
              requested_end = excluded.requested_end,
              effective_start = excluded.effective_start,
              effective_end = excluded.effective_end,
              fresh_through = excluded.fresh_through,
              row_count = excluded.row_count,
              metrics = excluded.metrics,
              source_status = excluded.source_status,
              warnings = excluded.warnings,
              payload_checksum = excluded.payload_checksum,
              captured_at = now()
            """,
            (
                plan_id, source, window_type, requested_start, requested_end,
                effective_start, effective_end, fresh_through, row_count, Jsonb(metrics),
                source_status, Jsonb(warnings), checksum,
            ),
        )
        connection.commit()
    return checksum


def upsert_outcome(
    settings: Settings,
    *,
    plan_id: UUID,
    classification: str,
    confidence: str,
    gsc_deltas: list[dict[str, Any]],
    evidence_summary: dict[str, Any],
    limitations: list[str],
    warnings: list[str],
    algorithm_version: str,
    ga4_deltas: list[dict[str, Any]] | None = None,
) -> None:
    """Store the conclusion. One outcome per plan; re-evaluating replaces it.

    `ga4_deltas` empty means the engagement layer was unavailable for this client — refused,
    unconnected, or unmapped. Downstream that reads as `unavailable`, never as zero engagement."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            insert into public.ai_visibility_measurement_outcomes(
              plan_id, classification, confidence, gsc_deltas, ga4_deltas, evidence_summary,
              limitations, warnings, algorithm_version
            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (plan_id) do update set
              classification = excluded.classification,
              confidence = excluded.confidence,
              gsc_deltas = excluded.gsc_deltas,
              ga4_deltas = excluded.ga4_deltas,
              evidence_summary = excluded.evidence_summary,
              limitations = excluded.limitations,
              warnings = excluded.warnings,
              algorithm_version = excluded.algorithm_version,
              evaluated_at = now()
            """,
            (
                plan_id, classification, confidence, Jsonb(gsc_deltas), Jsonb(ga4_deltas or []),
                Jsonb(evidence_summary), Jsonb(limitations), Jsonb(warnings), algorithm_version,
            ),
        )
        connection.commit()


def load_plans(
    settings: Settings, *, client_id: UUID | None = None, status: str | None = None
) -> list[StoredPlan]:
    clauses: list[str] = []
    params: list[Any] = []
    if client_id is not None:
        clauses.append("client_id = %s")
        params.append(client_id)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(
            f"select * from public.ai_visibility_measurement_plans {where} "
            "order by follow_up_end desc",
            tuple(params),
        ).fetchall()
    return [_to_plan(row) for row in rows]


def load_outcome_summary(settings: Settings, *, client_id: UUID) -> list[dict[str, Any]]:
    """Plans joined to their outcomes, for reporting and for the north-star funnel."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(
            """
            select plan.subject_id, plan.status as plan_status, plan.anchor_at,
                   plan.baseline_start, plan.follow_up_end,
                   plan.target_queries, plan.target_pages,
                   outcome.classification, outcome.confidence, outcome.gsc_deltas,
                   outcome.limitations, outcome.evaluated_at
              from public.ai_visibility_measurement_plans plan
              left join public.ai_visibility_measurement_outcomes outcome
                     on outcome.plan_id = plan.id
             where plan.client_id = %s
             order by plan.follow_up_end desc
            """,
            (client_id,),
        ).fetchall()
    return [dict(row) for row in rows]
