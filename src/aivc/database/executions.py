from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect

SUBJECT_RECOMMENDATION = "recon_recommendation"

EXECUTED_STATUSES = frozenset({"executed", "verified"})
VALID_STATUSES = frozenset({"proposed", "accepted", "executed", "verified", "abandoned"})
VALID_VERIFICATIONS = frozenset({"unverified", "snapshot_confirmed", "manual_confirmed"})


@dataclass(frozen=True)
class ActionExecution:
    subject_type: str
    subject_id: str
    client_id: UUID
    status: str
    implemented_at: datetime | None
    implemented_by: str | None
    target_pages: list[str]
    target_queries: list[str]
    action_type: str
    verification_status: str

    @property
    def is_measurable(self) -> bool:
        """True when an outcome window can legitimately be counted from this record.

        Requires both an executed status and a timestamp: without the anchor a measurement would be
        dated from an arbitrary moment, attributing pre-existing movement to the action."""
        return self.status in EXECUTED_STATUSES and self.implemented_at is not None


def _row_to_execution(row: dict[str, Any]) -> ActionExecution:
    return ActionExecution(
        subject_type=str(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        client_id=UUID(str(row["client_id"])),
        status=str(row["status"]),
        implemented_at=row["implemented_at"],
        implemented_by=row["implemented_by"],
        target_pages=list(row["target_pages"] or []),
        target_queries=list(row["target_queries"] or []),
        action_type=str(row["action_type"]),
        verification_status=str(row["verification_status"]),
    )


def record_execution(
    settings: Settings,
    *,
    subject_id: str,
    client_id: UUID,
    status: str = "executed",
    implemented_at: datetime | None = None,
    implemented_by: str | None = None,
    target_pages: list[str] | None = None,
    target_queries: list[str] | None = None,
    action_type: str = "other",
    implementation_notes: str | None = None,
    evidence_urls: list[str] | None = None,
    subject_type: str = SUBJECT_RECOMMENDATION,
) -> ActionExecution:
    """Record (or update) that a recommendation was implemented.

    Idempotent on (subject_type, subject_id): re-recording corrects an earlier entry rather than
    creating a second one, so the measurement anchor for a subject is always single-valued."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown execution status: {status!r}")
    if status in EXECUTED_STATUSES and implemented_at is None:
        raise ValueError(
            f"status={status!r} requires --implemented-at: an outcome window cannot be "
            "anchored without the date the change actually shipped."
        )

    with connect(settings) as connection, connection.cursor() as cursor:
        row = cursor.execute(
            """
            insert into public.aivc_action_executions(
              subject_type, subject_id, client_id, status, implemented_at, implemented_by,
              target_pages, target_queries, action_type, implementation_notes, evidence_urls
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (subject_type, subject_id) do update set
              status = excluded.status,
              implemented_at = excluded.implemented_at,
              implemented_by = coalesce(excluded.implemented_by,
                                        public.aivc_action_executions.implemented_by),
              target_pages = excluded.target_pages,
              target_queries = excluded.target_queries,
              action_type = excluded.action_type,
              implementation_notes = coalesce(excluded.implementation_notes,
                                              public.aivc_action_executions.implementation_notes),
              evidence_urls = excluded.evidence_urls,
              updated_at = now()
            returning *
            """,
            (
                subject_type,
                subject_id,
                client_id,
                status,
                implemented_at,
                implemented_by,
                Jsonb(target_pages or []),
                Jsonb(target_queries or []),
                action_type,
                implementation_notes,
                Jsonb(evidence_urls or []),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Execution record insert returned no row.")
        connection.commit()
    return _row_to_execution(row)


def verify_execution(
    settings: Settings,
    *,
    subject_id: str,
    verification_status: str = "manual_confirmed",
    subject_type: str = SUBJECT_RECOMMENDATION,
) -> ActionExecution:
    """Mark a recorded execution as verified. Never invents an implemented_at."""
    if verification_status not in VALID_VERIFICATIONS:
        raise ValueError(f"Unknown verification status: {verification_status!r}")
    with connect(settings) as connection, connection.cursor() as cursor:
        row = cursor.execute(
            """
            update public.aivc_action_executions
               set verification_status = %s,
                   status = case when status = 'executed' then 'verified' else status end,
                   verified_at = now(),
                   updated_at = now()
             where subject_type = %s and subject_id = %s
            returning *
            """,
            (verification_status, subject_type, subject_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"No execution record for {subject_type}:{subject_id}")
        connection.commit()
    return _row_to_execution(row)


def load_execution(
    settings: Settings, *, subject_id: str, subject_type: str = SUBJECT_RECOMMENDATION
) -> ActionExecution | None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        row = cursor.execute(
            """
            select * from public.aivc_action_executions
             where subject_type = %s and subject_id = %s
            """,
            (subject_type, subject_id),
        ).fetchone()
    return _row_to_execution(row) if row else None


def list_executions(
    settings: Settings, *, client_id: UUID | None = None, status: str | None = None
) -> list[ActionExecution]:
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
            f"""
            select * from public.aivc_action_executions
            {where}
             order by coalesce(implemented_at, created_at) desc
            """,
            tuple(params),
        ).fetchall()
    return [_row_to_execution(row) for row in rows]
