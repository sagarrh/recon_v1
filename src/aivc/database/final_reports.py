from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from aivc.contracts.models import SignalBundle, stable_id
from aivc.reporting.models import ArtifactManifest, FinalReportSnapshot


def persist_final_report(
    settings: Settings,
    snapshot: FinalReportSnapshot,
    manifest: ArtifactManifest,
) -> UUID:
    """Persist one sealed report snapshot and its verified artifact manifest."""
    snapshot.verify_checksum()
    if manifest.snapshot_checksum != snapshot.checksum:
        raise ValueError("artifact manifest does not belong to the report snapshot")
    input_checksum = stable_id(*sorted(snapshot.source_bundle_checksums.values()))
    values = (
        snapshot.idempotency_key,
        snapshot.client.client_id,
        snapshot.config.report_profile,
        snapshot.config.report_audience,
        snapshot.schema_version,
        snapshot.config.config_version,
        snapshot.config.report_config_hash,
        input_checksum,
        Jsonb(snapshot.source_bundle_ids),
        Jsonb(snapshot.source_bundle_checksums),
        snapshot.status.value,
        Jsonb(snapshot.model_dump(mode="json")),
        Jsonb(manifest.model_dump(mode="json")),
        Jsonb(snapshot.data_quality_flags),
        snapshot.generated_at,
    )
    with connect(settings) as connection, connection.cursor() as cursor:
        row = cursor.execute(
            """
            with target as (
              select id from public.aivc_final_reports
              where parent_run_id = %s
              order by generated_at desc nulls last, updated_at desc
              limit 1
            )
            update public.aivc_final_reports set
              idempotency_key = %s,
              client_id = %s,
              report_profile = %s,
              report_audience = %s,
              schema_version = %s,
              config_version = %s,
              report_config_hash = %s,
              input_checksum = %s,
              source_bundle_ids = %s,
              source_bundle_checksums = %s,
              status = %s,
              structured_snapshot = %s,
              artifact_manifest = %s,
              data_quality_flags = %s,
              generated_at = %s,
              last_error = null,
              updated_at = now()
            where id = (select id from target)
            returning id
            """,
            (snapshot.parent_run_id, *values),
        ).fetchone()
        if row is None:
            row = cursor.execute(
                """
            insert into public.aivc_final_reports(
              idempotency_key, parent_run_id, client_id, report_profile, report_audience,
              schema_version, config_version, report_config_hash, input_checksum,
              source_bundle_ids, source_bundle_checksums, status,
              structured_snapshot, artifact_manifest, data_quality_flags, generated_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            returning id
            """,
                (values[0], snapshot.parent_run_id, *values[1:]),
            ).fetchone()
        if row is None:
            raise RuntimeError("Final report upsert returned no ID.")
        connection.commit()
    return UUID(str(row["id"]))


def mark_final_report_failed(
    settings: Settings,
    *,
    idempotency_key: str,
    error: Exception,
) -> None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            update public.aivc_final_reports
            set status = 'failed', last_error = %s, updated_at = now()
            where idempotency_key = %s
            """,
            (str(error)[:4000], idempotency_key),
        )
        connection.commit()


def _snapshot_from_row(row: dict[str, Any] | None) -> FinalReportSnapshot | None:
    if row is None or row.get("structured_snapshot") is None:
        return None
    payload = row["structured_snapshot"]
    if not isinstance(payload, dict):
        raise ValueError("stored final report snapshot must be a JSON object")
    expected = payload.get("checksum")
    canonical = {
        key: value
        for key, value in payload.items()
        if key not in {"checksum", "generated_at"}
    }
    actual = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if not expected or expected != actual:
        raise ValueError("stored final report snapshot checksum is missing or invalid")
    return FinalReportSnapshot.model_validate(payload)


def load_final_report_by_parent(
    settings: Settings,
    parent_run_id: UUID,
) -> FinalReportSnapshot | None:
    query = """
        select structured_snapshot from public.aivc_final_reports
        where parent_run_id = %s
        order by generated_at desc nulls last, created_at desc limit 1
    """
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        row = cursor.execute(query, (parent_run_id,)).fetchone()
    return _snapshot_from_row(dict(row) if row else None)


def load_signal_bundles_for_parent(
    settings: Settings, parent_run_id: UUID
) -> dict[str, SignalBundle]:
    """Load exact immutable producer payloads for historical reproduction."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(
            """
            select producer, payload from public.aivc_signal_bundles
            where parent_run_id = %s
              and producer in ('ai_visibility', 'scout')
            order by created_at
            """,
            (parent_run_id,),
        ).fetchall()
    bundles: dict[str, SignalBundle] = {}
    for row in rows:
        bundle = SignalBundle.model_validate(row["payload"])
        bundle.verify_checksum()
        bundles[str(row["producer"])] = bundle
    return bundles


def resolve_latest_evidence_parent(
    settings: Settings,
    *,
    client_id: UUID | None = None,
    company_name: str | None = None,
) -> UUID:
    """Resolve the newest parent containing both immutable producer bundles."""
    if (client_id is None) == (company_name is None):
        raise ValueError("Provide exactly one client identity for evidence resolution.")
    condition = (
        "pr.client_id = %s"
        if client_id is not None
        else "lower(pr.canonical_name) = lower(%s)"
    )
    identity: object = client_id if client_id is not None else str(company_name).strip()
    query = f"""
        select pr.id, pr.client_id, pr.canonical_name
        from public.aivc_pipeline_runs pr
        where {condition}
          and pr.status in ('completed', 'partial')
          and (
            select count(distinct bundle.producer)
            from public.aivc_signal_bundles bundle
            where bundle.parent_run_id = pr.id
              and bundle.producer in ('ai_visibility', 'scout')
          ) = 2
        order by coalesce(pr.completed_at, pr.created_at) desc
        limit 20
    """
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(query, (identity,)).fetchall()
    if not rows:
        label = client_id or company_name
        raise LookupError(f"No complete persisted evidence parent was found for {label}.")
    identities = {str(row["client_id"]) for row in rows}
    if company_name is not None and len(identities) > 1:
        raise LookupError(
            f"Company '{company_name}' is ambiguous across {len(identities)} clients; "
            "use --client-id or --parent-run-id."
        )
    return UUID(str(rows[0]["id"]))
