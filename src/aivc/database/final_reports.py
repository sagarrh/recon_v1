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
    with connect(settings) as connection, connection.cursor() as cursor:
        row = cursor.execute(
            """
            insert into public.aivc_final_reports(
              idempotency_key, parent_run_id, client_id, report_profile,
              schema_version, config_version, report_config_hash, input_checksum,
              source_bundle_ids, source_bundle_checksums, status,
              structured_snapshot, artifact_manifest, data_quality_flags, generated_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict(idempotency_key) do update set
              status = excluded.status,
              structured_snapshot = excluded.structured_snapshot,
              artifact_manifest = excluded.artifact_manifest,
              data_quality_flags = excluded.data_quality_flags,
              generated_at = excluded.generated_at,
              last_error = null,
              updated_at = now()
            returning id
            """,
            (
                snapshot.idempotency_key,
                snapshot.parent_run_id,
                snapshot.client.client_id,
                snapshot.config.report_profile.value,
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
            ),
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
    *,
    profile: str | None = None,
) -> FinalReportSnapshot | None:
    parameters: tuple[object, ...]
    if profile is None:
        query = """
            select structured_snapshot from public.aivc_final_reports
            where parent_run_id = %s
            order by generated_at desc nulls last, created_at desc limit 1
        """
        parameters = (parent_run_id,)
    else:
        query = """
            select structured_snapshot from public.aivc_final_reports
            where parent_run_id = %s and report_profile = %s
            order by generated_at desc nulls last, created_at desc limit 1
        """
        parameters = (parent_run_id, profile)
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        row = cursor.execute(query, parameters).fetchone()
    return _snapshot_from_row(dict(row) if row else None)


def load_latest_final_report(
    settings: Settings,
    client_id: UUID,
    *,
    profile: str | None = None,
) -> FinalReportSnapshot | None:
    parameters: tuple[object, ...]
    if profile is None:
        query = """
            select structured_snapshot from public.aivc_final_reports
            where client_id = %s
            order by generated_at desc nulls last, created_at desc limit 1
        """
        parameters = (client_id,)
    else:
        query = """
            select structured_snapshot from public.aivc_final_reports
            where client_id = %s and report_profile = %s
            order by generated_at desc nulls last, created_at desc limit 1
        """
        parameters = (client_id, profile)
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        row = cursor.execute(query, parameters).fetchone()
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
