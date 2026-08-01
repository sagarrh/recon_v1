from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.migrations import apply_migrations
from aivc.config.settings import AivcSettings
from aivc.database.final_reports import (
    load_signal_bundles_for_parent,
    mark_final_report_failed,
    persist_final_report,
)
from aivc.database.orchestration import finish_pipeline_run, set_stage
from aivc.reporting.artifacts import (
    refresh_latest_artifacts,
    write_final_report_artifacts,
)
from aivc.reporting.config import ReportProfile, load_report_config
from aivc.reporting.models import ArtifactManifest, FinalReportSnapshot
from aivc.reporting.narrative import ReuseValidatedNarrative
from aivc.reporting.snapshot import build_final_report_snapshot
from aivc.reporting.validation import validate_final_report_payload
from scout.config import get_config

from .pipeline import run_integrated_pipeline


@dataclass(frozen=True)
class FinalReportRunResult:
    snapshot: FinalReportSnapshot
    manifest: ArtifactManifest
    database_report_id: UUID
    artifact_paths: dict[str, Path]
    latest_paths: dict[str, Path]


def _citation_report_from_bundle(bundle_path: str | None) -> dict[str, object]:
    if not bundle_path:
        raise RuntimeError("Exact citation report path is missing from the source bundle.")
    path = Path(bundle_path)
    if not path.is_file():
        raise RuntimeError(f"Exact citation report artifact is unavailable: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Citation report artifact must contain a JSON object.")
    return payload


def _write_persist_and_refresh(
    settings: Settings,
    snapshot: FinalReportSnapshot,
    *,
    write_latest: bool,
) -> FinalReportRunResult:
    validate_final_report_payload(snapshot.model_dump(mode="json"))
    manifest, artifact_paths = write_final_report_artifacts(
        settings.report_output_dir,
        snapshot,
        write_latest_copies=False,
    )
    database_report_id = persist_final_report(settings, snapshot, manifest)
    try:
        latest_paths = (
            refresh_latest_artifacts(settings.report_output_dir, snapshot)
            if write_latest
            else {}
        )
    except Exception as exc:
        mark_final_report_failed(
            settings,
            idempotency_key=snapshot.idempotency_key,
            error=exc,
        )
        raise
    return FinalReportRunResult(
        snapshot=snapshot,
        manifest=manifest,
        database_report_id=database_report_id,
        artifact_paths=artifact_paths,
        latest_paths=latest_paths,
    )


def run_final_report_pipeline(
    settings: Settings,
    shared_settings: AivcSettings,
    *,
    company_name: str | None = None,
    client_id: UUID | None = None,
    profile: ReportProfile | str | None = None,
    config_path: Path | None = None,
    allow_partial: bool | None = None,
) -> FinalReportRunResult:
    shared_settings.require_supabase_key()
    recon_config = get_config()
    if not recon_config.openrouter_api_key.strip():
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for the Recon synthesis stages."
        )
    config = load_report_config(config_path=config_path, profile=profile)
    integrated = run_integrated_pipeline(
        settings,
        shared_settings,
        company_name,
        client_id=client_id,
        finish_parent=False,
        deliver=False,
    )
    parent_run_id = integrated.parent_run_id
    current_stage = "report_preflight"
    try:
        set_stage(settings, parent_run_id, current_stage, "running")
        integrated.citation_bundle.verify_checksum()
        integrated.recon_bundle.verify_checksum()
        integrated.combined_bundle.verify_checksum()
        set_stage(settings, parent_run_id, current_stage, "completed")

        current_stage = "publication_gate"
        set_stage(settings, parent_run_id, current_stage, "running")
        set_stage(settings, parent_run_id, current_stage, "completed")

        current_stage = "decision_cards"
        set_stage(settings, parent_run_id, current_stage, "running")
        snapshot = build_final_report_snapshot(
            parent_run_id=parent_run_id,
            citation_report=integrated.citation_report,
            citation_bundle=integrated.citation_bundle,
            recon_bundle=integrated.recon_bundle,
            combined_bundle=integrated.combined_bundle,
            config=config,
        )
        snapshot = ReuseValidatedNarrative().enrich(snapshot)
        set_stage(
            settings,
            parent_run_id,
            current_stage,
            "completed",
            output_checksum=snapshot.checksum,
        )

        current_stage = "report_snapshot"
        set_stage(settings, parent_run_id, current_stage, "running")
        validate_final_report_payload(snapshot.model_dump(mode="json"))
        set_stage(
            settings,
            parent_run_id,
            current_stage,
            "completed",
            artifact_id=snapshot.report_id,
            output_checksum=snapshot.checksum,
        )

        current_stage = "report_render"
        set_stage(settings, parent_run_id, current_stage, "running")
        manifest, artifact_paths = write_final_report_artifacts(
            settings.report_output_dir,
            snapshot,
            write_latest_copies=False,
        )
        set_stage(settings, parent_run_id, current_stage, "completed")

        current_stage = "report_persist"
        set_stage(settings, parent_run_id, current_stage, "running")
        database_report_id = persist_final_report(settings, snapshot, manifest)
        partial_allowed = (
            allow_partial
            if allow_partial is not None
            else config.config.report.allow_partial
        )
        publishable = snapshot.status.value == "complete" or (
            snapshot.status.value == "partial" and partial_allowed
        )
        try:
            latest_paths = (
                refresh_latest_artifacts(settings.report_output_dir, snapshot)
                if config.config.report.write_latest_copies and publishable
                else {}
            )
        except Exception as exc:
            mark_final_report_failed(
                settings,
                idempotency_key=snapshot.idempotency_key,
                error=exc,
            )
            raise
        set_stage(
            settings,
            parent_run_id,
            current_stage,
            "completed",
            artifact_id=str(database_report_id),
            output_checksum=snapshot.checksum,
        )
        parent_status = "completed" if snapshot.status.value == "complete" else "partial"
        if snapshot.status.value == "blocked":
            parent_status = "failed"
        finish_pipeline_run(
            settings,
            parent_run_id,
            parent_status,
            combined_bundle=integrated.combined_bundle,
        )
        return FinalReportRunResult(
            snapshot=snapshot,
            manifest=manifest,
            database_report_id=database_report_id,
            artifact_paths=artifact_paths,
            latest_paths=latest_paths,
        )
    except Exception as exc:
        set_stage(settings, parent_run_id, current_stage, "failed", error=exc)
        finish_pipeline_run(settings, parent_run_id, "failed", error=exc)
        raise


def render_historical_report(
    settings: Settings,
    *,
    parent_run_id: UUID,
    profile: ReportProfile | str,
    config_path: Path | None = None,
    allow_partial: bool | None = None,
) -> FinalReportRunResult:
    """Render exact persisted sources without rerunning either producer."""
    apply_migrations(settings)
    config = load_report_config(config_path=config_path, profile=profile)
    partial_allowed = (
        allow_partial if allow_partial is not None else config.config.report.allow_partial
    )
    bundles = load_signal_bundles_for_parent(settings, parent_run_id)
    required = {"ai_visibility", "scout", "aivc_combined"}
    missing = sorted(required - set(bundles))
    if missing:
        raise RuntimeError(f"Historical source bundles are missing: {', '.join(missing)}")
    citation = bundles["ai_visibility"]
    reference = next(
        (item for item in citation.reports if item.report_type == "company_intelligence_report"),
        None,
    )
    citation_report = _citation_report_from_bundle(reference.json_path if reference else None)
    snapshot = build_final_report_snapshot(
        parent_run_id=parent_run_id,
        citation_report=citation_report,
        citation_bundle=citation,
        recon_bundle=bundles["scout"],
        combined_bundle=bundles["aivc_combined"],
        config=config,
    )
    snapshot = ReuseValidatedNarrative().enrich(snapshot)
    publishable = snapshot.status.value == "complete" or (
        snapshot.status.value == "partial" and partial_allowed
    )
    return _write_persist_and_refresh(
        settings,
        snapshot,
        write_latest=config.config.report.write_latest_copies and publishable,
    )
