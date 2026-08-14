from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer

from ai_visibility.config.settings import get_settings
from ai_visibility.database.validation import check_database
from aivc.config import get_aivc_settings
from aivc.database import audit_measurement_foundation, audit_shared_schema
from aivc.database.evidence import resolve_latest_evidence_parent
from aivc.measurement.models import (
    StartMeasurementRequest,
    SubjectType,
    VerificationStatus,
)
from aivc.measurement.repository import (
    measurement_plan_status,
    run_measurement_plan,
    start_measurement,
)
from aivc.orchestration import (
    prepare_fresh_report_inputs,
    prepare_historical_report_inputs,
    run_integrated_pipeline,
)
from aivc.producers import generate_citation_bundle

app = typer.Typer(
    name="aivc",
    help="Run the integrated AIVC intelligence backend.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Validate shared database configuration.")
citations_app = typer.Typer(help="Run the AI citation producer independently.")
report_app = typer.Typer(help="Prepare compact Citation + Recon inputs for a client report.")
measurement_app = typer.Typer(help="Validate and measure GSC/GA4 outcomes for ReconV1 actions.")
app.add_typer(db_app, name="db")
app.add_typer(citations_app, name="citations")
app.add_typer(report_app, name="report")
app.add_typer(measurement_app, name="measurement")


def _print(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, default=str, ensure_ascii=False))


def _run[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except (RuntimeError, ValueError, LookupError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


@db_app.command("check")
def db_check() -> None:
    """Check project consistency and the immutable citation source."""

    def action() -> dict[str, Any]:
        project = get_aivc_settings().validate_same_project()
        return {"project": project, "citation_database": check_database(get_settings())}

    _print(_run(action))


@db_app.command("audit")
def db_audit() -> None:
    """Read-only audit of citation and Recon tables, identity, and freshness."""
    _print(_run(lambda: audit_shared_schema(get_settings())))


@measurement_app.command("check")
def measurement_check(
    company: Annotated[
        str | None, typer.Option("--company", help="Exact client company name.")
    ] = None,
    client_id: Annotated[
        UUID | None, typer.Option("--client-id", help="Exact authoritative client UUID.")
    ] = None,
) -> None:
    """Read-only readiness audit for one client's mirrored GSC and GA4 data."""
    if (company is None) == (client_id is None):
        raise typer.BadParameter("Provide exactly one of --company or --client-id.")
    cfg = get_aivc_settings()
    result = _run(
        lambda: audit_measurement_foundation(
            get_settings(),
            client_id=client_id,
            company_name=company,
            gsc_max_lag_days=cfg.aivc_measurement_gsc_max_lag_days,
            ga4_max_lag_days=cfg.aivc_measurement_ga4_max_lag_days,
        )
    )
    _print(result.model_dump(mode="json"))


@measurement_app.command("start")
def measurement_start(
    client_id: Annotated[
        UUID, typer.Option("--client-id", help="Exact authoritative client UUID.")
    ],
    subject_type: Annotated[
        SubjectType,
        typer.Option("--subject-type", help="Implemented recommendation or citation signal."),
    ],
    subject_id: Annotated[
        str, typer.Option("--subject-id", help="Exact persisted recommendation/signal UUID.")
    ],
    implemented_at: Annotated[
        datetime,
        typer.Option("--implemented-at", help="Timezone-aware implementation timestamp."),
    ],
    implemented_by: Annotated[
        str,
        typer.Option("--implemented-by", help="Person or system confirming implementation."),
    ],
    target_page: Annotated[
        list[str] | None,
        typer.Option("--target-page", help="Exact affected page; repeat for multiple pages."),
    ] = None,
    target_query: Annotated[
        list[str] | None,
        typer.Option("--target-query", help="Exact affected GSC query; repeat as needed."),
    ] = None,
    evidence_url: Annotated[
        list[str] | None,
        typer.Option("--evidence-url", help="Implementation evidence URL; repeat as needed."),
    ] = None,
    action_type: Annotated[
        str, typer.Option("--action-type", help="Implemented action classification.")
    ] = "other",
    verification_status: Annotated[
        VerificationStatus,
        typer.Option("--verification-status", help="How implementation was confirmed."),
    ] = VerificationStatus.manual_confirmed,
    implementation_notes: Annotated[
        str | None, typer.Option("--notes", help="Optional implementation notes.")
    ] = None,
    require_ga4: Annotated[
        bool,
        typer.Option("--require-ga4", help="Make GA4 a required rather than optional source."),
    ] = False,
) -> None:
    """Register a verified action and create fixed measurement windows."""
    cfg = get_aivc_settings()
    request = _run(
        lambda: StartMeasurementRequest(
            client_id=client_id,
            subject_type=subject_type,
            subject_id=subject_id,
            implemented_at=implemented_at,
            implemented_by=implemented_by,
            verification_status=verification_status,
            action_type=action_type,
            target_pages=target_page or [],
            target_queries=target_query or [],
            evidence_urls=evidence_url or [],
            implementation_notes=implementation_notes,
            baseline_days=cfg.aivc_measurement_baseline_days,
            stabilization_days=cfg.aivc_measurement_stabilization_days,
            follow_up_days=cfg.aivc_measurement_follow_up_days,
            require_ga4=require_ga4,
        )
    )
    result = _run(lambda: start_measurement(get_settings(), cfg, request))
    _print(result.model_dump(mode="json"))


@measurement_app.command("run")
def measurement_run(
    plan_id: Annotated[UUID, typer.Option("--plan-id", help="Measurement plan UUID.")],
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Optional ISO date (YYYY-MM-DD) for controlled backfills."),
    ] = None,
) -> None:
    """Capture eligible windows and evaluate the plan when evidence is ready."""
    parsed_as_of = _run(lambda: date.fromisoformat(as_of)) if as_of else None
    _print(
        _run(
            lambda: run_measurement_plan(
                get_settings(), get_aivc_settings(), plan_id=plan_id, as_of=parsed_as_of
            )
        )
    )


@measurement_app.command("status")
def measurement_status(
    plan_id: Annotated[UUID, typer.Option("--plan-id", help="Measurement plan UUID.")],
) -> None:
    """Show compact plan, snapshot, and outcome status without raw source rows."""
    _print(_run(lambda: measurement_plan_status(get_settings(), plan_id=plan_id)))


@citations_app.command("generate")
def citations_generate(
    company: Annotated[str, typer.Option("--company", help="Exact client company name.")],
) -> None:
    """Run the existing citation report through the shared CLI."""
    result, bundle_path, bundle = _run(lambda: generate_citation_bundle(get_settings(), company))
    _print(
        {
            "report_status": "complete",
            "bundle_status": bundle.status,
            "bundle_id": bundle.bundle_id,
            "bundle_checksum": bundle.checksum,
            "report_id": result.report_id,
            "json_path": result.json_path,
            "markdown_path": result.markdown_path,
            "valid_run_count": result.valid_run_count,
            "invalid_run_count": result.invalid_run_count,
            "queued_page_count": result.queued_page_count,
            "bundle_path": bundle_path,
        }
    )


@app.command("run")
def integrated_run(
    company: Annotated[
        str | None, typer.Option("--company", help="Exact client company name.")
    ] = None,
    client_id: Annotated[
        UUID | None, typer.Option("--client-id", help="Exact authoritative client UUID.")
    ] = None,
) -> None:
    """Run citation, Recon, persistence, delivery, and bundle composition."""
    if (company is None) == (client_id is None):
        raise typer.BadParameter("Provide exactly one of --company or --client-id.")
    result = _run(
        lambda: run_integrated_pipeline(
            get_settings(), get_aivc_settings(), company, client_id=client_id
        )
    )
    _print(
        {
            "status": (
                "complete"
                if result.citation_bundle.status.value == "complete"
                and result.recon_bundle.status.value == "complete"
                else "partial"
            ),
            "parent_run_id": result.parent_run_id,
            "citation_bundle_path": result.citation_bundle_path,
            "recon_bundle_path": result.recon_bundle_path,
            "citation_bundle_id": result.citation_bundle.bundle_id,
            "recon_bundle_id": result.recon_bundle.bundle_id,
        }
    )


def _report_result(result: Any) -> dict[str, Any]:
    snapshot = result.snapshot
    return {
        "status": snapshot.status,
        "client_id": snapshot.client.client_id,
        "company": snapshot.client.canonical_name,
        "parent_run_id": snapshot.parent_run_id,
        "report_input_checksum": result.report_input.checksum,
        "source_bundle_ids": snapshot.source_bundle_ids,
        "source_bundle_checksums": snapshot.source_bundle_checksums,
        "decision_card_count": len(snapshot.decision_cards),
        "action_count": len(snapshot.consolidated_actions),
        "data_quality_flags": snapshot.data_quality_flags,
        "limitations": snapshot.limitations,
        "inputs": {
            record.artifact_type: {
                "path": record.path,
                "sha256": record.sha256,
                "byte_size": record.byte_size,
            }
            for record in result.manifest.artifacts
        },
        "latest_inputs_refreshed": bool(result.latest_paths),
        "manual_report_prompt": "output/docs/prompts/CLIENT_REPORT_GENERATION_PROMPT.md",
    }


@report_app.command("generate")
def report_generate(
    parent_run_id: Annotated[
        UUID | None,
        typer.Option(
            "--parent-run-id",
            help="Exact persisted evidence parent. No producer rerun is performed.",
        ),
    ] = None,
    company: Annotated[
        str | None, typer.Option("--company", help="Exact client company name.")
    ] = None,
    client_id: Annotated[
        UUID | None, typer.Option("--client-id", help="Exact authoritative client UUID.")
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Reporting TOML configuration path."),
    ] = None,
    refresh_data: Annotated[
        bool,
        typer.Option(
            "--refresh-data",
            help="Explicitly rerun Citation and Recon before preparing report inputs.",
        ),
    ] = False,
) -> None:
    """Prepare compact report inputs; refresh Citation and Recon only on request."""
    if refresh_data:
        if parent_run_id is not None:
            raise typer.BadParameter("--refresh-data cannot be combined with --parent-run-id.")
        if (company is None) == (client_id is None):
            raise typer.BadParameter(
                "With --refresh-data, provide exactly one of --company or --client-id."
            )
        result = _run(
            lambda: prepare_fresh_report_inputs(
                get_settings(),
                get_aivc_settings(),
                company_name=company,
                client_id=client_id,
                config_path=config,
            )
        )
    else:
        if parent_run_id is not None and (company is not None or client_id is not None):
            raise typer.BadParameter(
                "Use --parent-run-id by itself, or identify the latest evidence by company/client."
            )
        if parent_run_id is None:
            if (company is None) == (client_id is None):
                raise typer.BadParameter(
                    "Provide --parent-run-id, --company, or --client-id. "
                    "Add --refresh-data only when a new producer run is intended."
                )
            parent_run_id = _run(
                lambda: resolve_latest_evidence_parent(
                    get_settings(),
                    client_id=client_id,
                    company_name=company,
                )
            )
        result = _run(
            lambda: prepare_historical_report_inputs(
                get_settings(),
                parent_run_id=parent_run_id,
                config_path=config,
            )
        )
    _print(_report_result(result))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
