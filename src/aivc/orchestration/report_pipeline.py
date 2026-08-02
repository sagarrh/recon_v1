from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.migrations import apply_migrations
from aivc.config.settings import AivcSettings
from aivc.contracts.models import SignalBundle
from aivc.database.evidence import load_signal_bundles_for_parent
from aivc.database.orchestration import finish_pipeline_run, set_stage
from aivc.database.recon_reporting import load_recon_reporting_payload
from aivc.reporting.artifacts import write_report_inputs
from aivc.reporting.config import load_report_config
from aivc.reporting.context import ReportInputSnapshot, build_report_input
from aivc.reporting.models import ArtifactManifest, FinalReportSnapshot
from aivc.reporting.snapshot import build_final_report_snapshot
from scout.config import get_config

from .pipeline import run_integrated_pipeline


@dataclass(frozen=True)
class ReportInputRunResult:
    snapshot: FinalReportSnapshot
    report_input: ReportInputSnapshot
    manifest: ArtifactManifest
    artifact_paths: dict[str, Path]
    latest_paths: dict[str, Path]


def _report_week(*bundles: SignalBundle) -> date | None:
    ends = [
        bundle.analysis_period.end for bundle in bundles if bundle.analysis_period.end is not None
    ]
    return max(ends).date() if ends else None


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


def _build_and_write(
    settings: Settings,
    *,
    parent_run_id: UUID,
    citation_report: dict[str, object],
    citation_bundle: SignalBundle,
    recon_bundle: SignalBundle,
    config_path: Path | None,
) -> ReportInputRunResult:
    config = load_report_config(config_path=config_path)
    report_week = _report_week(citation_bundle, recon_bundle)
    recon_reporting = load_recon_reporting_payload(
        settings,
        client_id=citation_bundle.client.client_id,
        report_week=report_week,
        history_weeks=config.profile_settings.history_weeks,
    )
    snapshot = build_final_report_snapshot(
        parent_run_id=parent_run_id,
        citation_report=citation_report,
        citation_bundle=citation_bundle,
        recon_bundle=recon_bundle,
        recon_reporting=recon_reporting,
        config=config,
    )
    report_input = build_report_input(snapshot)
    manifest, artifact_paths, latest_paths = write_report_inputs(
        settings.report_output_dir,
        snapshot,
        report_input,
    )
    return ReportInputRunResult(
        snapshot=snapshot,
        report_input=report_input,
        manifest=manifest,
        artifact_paths=artifact_paths,
        latest_paths=latest_paths,
    )


def prepare_fresh_report_inputs(
    settings: Settings,
    shared_settings: AivcSettings,
    *,
    company_name: str | None = None,
    client_id: UUID | None = None,
    config_path: Path | None = None,
) -> ReportInputRunResult:
    """Run Citation + Recon, then write compact inputs without rendering a report."""
    shared_settings.require_supabase_key()
    if not get_config().openrouter_api_key.strip():
        raise RuntimeError("OPENROUTER_API_KEY is required for the Recon synthesis stages.")
    integrated = run_integrated_pipeline(
        settings,
        shared_settings,
        company_name,
        client_id=client_id,
        finish_parent=False,
        deliver=False,
    )
    parent_run_id = integrated.parent_run_id
    stage = "report_input"
    try:
        set_stage(settings, parent_run_id, stage, "running")
        integrated.citation_bundle.verify_checksum()
        integrated.recon_bundle.verify_checksum()
        result = _build_and_write(
            settings,
            parent_run_id=parent_run_id,
            citation_report=integrated.citation_report,
            citation_bundle=integrated.citation_bundle,
            recon_bundle=integrated.recon_bundle,
            config_path=config_path,
        )
        set_stage(
            settings,
            parent_run_id,
            stage,
            "completed",
            artifact_id=result.report_input.checksum,
            output_checksum=result.report_input.checksum,
        )
        parent_status = "completed" if result.snapshot.status.value == "complete" else "partial"
        finish_pipeline_run(settings, parent_run_id, parent_status)
        return result
    except Exception as exc:
        set_stage(settings, parent_run_id, stage, "failed", error=exc)
        finish_pipeline_run(settings, parent_run_id, "failed", error=exc)
        raise


def prepare_historical_report_inputs(
    settings: Settings,
    *,
    parent_run_id: UUID,
    config_path: Path | None = None,
) -> ReportInputRunResult:
    """Rebuild compact inputs from one exact persisted evidence parent."""
    apply_migrations(settings)
    bundles = load_signal_bundles_for_parent(settings, parent_run_id)
    missing = sorted({"ai_visibility", "scout"} - set(bundles))
    if missing:
        raise RuntimeError(f"Historical source bundles are missing: {', '.join(missing)}")
    citation = bundles["ai_visibility"]
    reference = next(
        (item for item in citation.reports if item.report_type == "company_intelligence_report"),
        None,
    )
    citation_report = _citation_report_from_bundle(reference.json_path if reference else None)
    return _build_and_write(
        settings,
        parent_run_id=parent_run_id,
        citation_report=citation_report,
        citation_bundle=citation,
        recon_bundle=bundles["scout"],
        config_path=config_path,
    )
