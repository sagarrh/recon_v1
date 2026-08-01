from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from aivc.contracts.models import ClientIdentity, SignalBundle


def create_pipeline_run(
    settings: Settings,
    client: ClientIdentity,
    *,
    requested_options: dict[str, Any],
    component_versions: dict[str, str],
) -> UUID:
    with connect(settings) as connection, connection.cursor() as cursor:
        row = cursor.execute(
            """
            insert into public.aivc_pipeline_runs(
              client_id, canonical_name, status, requested_options,
              component_versions, started_at
            ) values (%s, %s, 'running', %s, %s, now())
            returning id
            """,
            (
                client.client_id,
                client.canonical_name,
                Jsonb(requested_options),
                Jsonb(component_versions),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Pipeline run insert returned no ID.")
        connection.commit()
    return UUID(str(row["id"]))


def set_stage(
    settings: Settings,
    parent_run_id: UUID,
    stage_name: str,
    status: str,
    *,
    required: bool = True,
    child_run_id: str | None = None,
    artifact_id: str | None = None,
    input_checksum: str | None = None,
    output_checksum: str | None = None,
    error: Exception | None = None,
) -> None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            insert into public.aivc_pipeline_stages(
              parent_run_id, stage_name, attempt, status, required,
              child_run_id, artifact_id, input_checksum, output_checksum,
              started_at, completed_at, error_type, error_message
            ) values (
              %s, %s, 1, %s, %s, %s, %s, %s, %s,
              case when %s = 'running' then now() else null end,
              case when %s in ('completed','partial','failed','skipped') then now() else null end,
              %s, %s
            )
            on conflict(parent_run_id, stage_name, attempt) do update set
              status = excluded.status,
              required = excluded.required,
              child_run_id = coalesce(excluded.child_run_id, aivc_pipeline_stages.child_run_id),
              artifact_id = coalesce(excluded.artifact_id, aivc_pipeline_stages.artifact_id),
              input_checksum = coalesce(
                excluded.input_checksum, aivc_pipeline_stages.input_checksum
              ),
              output_checksum = coalesce(
                excluded.output_checksum, aivc_pipeline_stages.output_checksum
              ),
              started_at = coalesce(aivc_pipeline_stages.started_at, excluded.started_at),
              completed_at = excluded.completed_at,
              error_type = excluded.error_type,
              error_message = excluded.error_message,
              updated_at = now()
            """,
            (
                parent_run_id,
                stage_name,
                status,
                required,
                child_run_id,
                artifact_id,
                input_checksum,
                output_checksum,
                status,
                status,
                type(error).__name__ if error else None,
                str(error)[:4000] if error else None,
            ),
        )
        connection.commit()


def persist_signal_bundle(settings: Settings, bundle: SignalBundle) -> None:
    bundle.verify_checksum()
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            insert into public.aivc_signal_bundles(
              bundle_id, parent_run_id, producer, producer_run_id, client_id,
              schema_version, analysis_start, analysis_end, status, payload, checksum
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict(bundle_id) do update set
              parent_run_id = excluded.parent_run_id,
              status = excluded.status,
              payload = excluded.payload,
              checksum = excluded.checksum,
              updated_at = now()
            """,
            (
                bundle.bundle_id,
                bundle.producer.parent_run_id,
                bundle.producer.name,
                bundle.producer.run_id,
                bundle.client.client_id,
                bundle.schema_version,
                bundle.analysis_period.start,
                bundle.analysis_period.end,
                bundle.status.value,
                Jsonb(bundle.model_dump(mode="json")),
                bundle.checksum,
            ),
        )
        connection.commit()


def finish_pipeline_run(
    settings: Settings,
    parent_run_id: UUID,
    status: str,
    *,
    combined_bundle: SignalBundle | None = None,
    error: Exception | None = None,
) -> None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            update public.aivc_pipeline_runs set
              status = %s,
              completed_at = now(),
              error_summary = %s,
              combined_bundle_id = %s,
              combined_bundle_checksum = %s,
              updated_at = now()
            where id = %s
            """,
            (
                status,
                str(error)[:4000] if error else None,
                combined_bundle.bundle_id if combined_bundle else None,
                combined_bundle.checksum if combined_bundle else None,
                parent_run_id,
            ),
        )
        connection.commit()
