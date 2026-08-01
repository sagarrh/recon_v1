from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ai_visibility.companies.resolver import resolve_client, resolve_client_by_id
from ai_visibility.config.settings import Settings
from ai_visibility.database.migrations import apply_migrations
from ai_visibility.pipeline import generate_company_report
from aivc.bundles import compose_bundles
from aivc.config.settings import AivcSettings
from aivc.contracts.models import ClientIdentity, SignalBundle
from aivc.database.orchestration import (
    create_pipeline_run,
    finish_pipeline_run,
    persist_signal_bundle,
    set_stage,
)
from aivc.producers.citation_bundle import build_citation_bundle, write_bundle
from aivc.producers.recon_bundle import build_recon_bundle
from scout.runner import prepare_recon_data, run_recon


@dataclass(frozen=True)
class IntegratedRunResult:
    parent_run_id: UUID
    citation_bundle_path: Path
    recon_bundle_path: Path
    combined_bundle_path: Path
    citation_bundle: SignalBundle
    recon_bundle: SignalBundle
    combined_bundle: SignalBundle
    citation_report: dict[str, object]


def run_integrated_pipeline(
    settings: Settings,
    shared_settings: AivcSettings,
    company_name: str | None = None,
    *,
    client_id: UUID | None = None,
    finish_parent: bool = True,
    deliver: bool | None = None,
) -> IntegratedRunResult:
    """Run citation and Recon producers under one durable parent lifecycle."""
    shared_settings.validate_same_project()
    apply_migrations(settings)
    if (company_name is None) == (client_id is None):
        raise ValueError("Provide exactly one of company_name or client_id.")
    resolved = (
        resolve_client_by_id(settings, client_id)
        if client_id is not None
        else resolve_client(settings, str(company_name))
    )
    client = ClientIdentity(
        client_id=resolved.client_id,
        canonical_name=resolved.canonical_name,
        aliases=list(resolved.aliases),
        official_domains=list(resolved.official_domains),
    )
    parent_run_id = create_pipeline_run(
        settings,
        client,
        requested_options={
            "company": company_name,
            "client_id": str(client_id) if client_id else None,
        },
        component_versions={
            "aivc": shared_settings.pipeline_version,
            "ai_visibility": settings.pipeline_version,
            "scout": shared_settings.pipeline_version,
        },
    )
    set_stage(settings, parent_run_id, "preflight", "completed")

    try:
        set_stage(settings, parent_run_id, "citation", "running")
        set_stage(settings, parent_run_id, "recon_preparation", "running")
        with ThreadPoolExecutor(
            max_workers=min(shared_settings.aivc_max_parallel_stages, 2)
        ) as executor:
            citation_future = executor.submit(
                generate_company_report,
                settings,
                None,
                client_id=resolved.client_id,
            )
            recon_future = executor.submit(
                prepare_recon_data, client_id=str(resolved.client_id)
            )
            citation_result = citation_future.result()
            prepared_recon = recon_future.result()

        citation_bundle = build_citation_bundle(
            citation_result.normalized_runs,
            citation_result.report,
            producer_version=settings.pipeline_version,
            producer_run_id=str(citation_result.report_id),
            parent_run_id=parent_run_id,
            report_id=str(citation_result.report_id),
            json_path=citation_result.json_path,
            markdown_path=citation_result.markdown_path,
        )
        citation_path = write_bundle(
            citation_result.json_path.parent / "citation-signal-bundle.json",
            citation_bundle,
        )
        persist_signal_bundle(settings, citation_bundle)
        set_stage(
            settings,
            parent_run_id,
            "citation",
            "completed",
            child_run_id=str(citation_result.report_id),
            artifact_id=citation_bundle.bundle_id,
            output_checksum=citation_bundle.checksum,
        )
        set_stage(settings, parent_run_id, "recon_preparation", "completed")

        set_stage(
            settings,
            parent_run_id,
            "recon",
            "running",
            input_checksum=citation_bundle.checksum,
        )
        recon_state, persistence = run_recon(
            prepared_recon,
            citation_bundle,
            deliver=(
                shared_settings.aivc_delivery_mode == "configured"
                if deliver is None
                else deliver
            ),
        )
        recon_bundle = build_recon_bundle(
            recon_state,
            client,
            producer_version=shared_settings.pipeline_version,
            parent_run_id=parent_run_id,
            persistence_failures=persistence.get("failures", {}),
        )
        recon_path = write_bundle(
            citation_result.json_path.parent / "recon-signal-bundle.json", recon_bundle
        )
        persist_signal_bundle(settings, recon_bundle)
        set_stage(
            settings,
            parent_run_id,
            "recon",
            "completed" if not persistence.get("failures") else "partial",
            child_run_id=str(recon_state["run_id"]),
            artifact_id=recon_bundle.bundle_id,
            output_checksum=recon_bundle.checksum,
        )

        set_stage(settings, parent_run_id, "compose", "running")
        combined = compose_bundles(
            citation_bundle,
            recon_bundle,
            producer_version=shared_settings.pipeline_version,
            parent_run_id=parent_run_id,
        )
        combined_path = write_bundle(
            citation_result.json_path.parent / "combined-signal-bundle.json", combined
        )
        persist_signal_bundle(settings, combined)
        set_stage(
            settings,
            parent_run_id,
            "compose",
            "completed",
            artifact_id=combined.bundle_id,
            output_checksum=combined.checksum,
        )
        final_status = "completed" if combined.status.value == "complete" else "partial"
        if finish_parent:
            finish_pipeline_run(
                settings, parent_run_id, final_status, combined_bundle=combined
            )
        return IntegratedRunResult(
            parent_run_id=parent_run_id,
            citation_bundle_path=citation_path,
            recon_bundle_path=recon_path,
            combined_bundle_path=combined_path,
            citation_bundle=citation_bundle,
            recon_bundle=recon_bundle,
            combined_bundle=combined,
            citation_report=citation_result.report,
        )
    except Exception as exc:
        finish_pipeline_run(settings, parent_run_id, "failed", error=exc)
        raise
