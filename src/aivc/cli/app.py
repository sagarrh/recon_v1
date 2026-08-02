from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer

from ai_visibility.config.settings import get_settings
from ai_visibility.database.validation import check_database
from aivc.config import get_aivc_settings
from aivc.database import audit_shared_schema
from aivc.database.final_reports import (
    load_final_report_by_parent,
    resolve_latest_evidence_parent,
)
from aivc.orchestration import (
    render_historical_report,
    run_final_report_pipeline,
    run_integrated_pipeline,
)
from aivc.producers import generate_citation_bundle
from aivc.reporting.validation import validate_final_report_file

app = typer.Typer(
    name="aivc",
    help="Run the integrated AIVC intelligence backend.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Validate shared database configuration.")
citations_app = typer.Typer(help="Run the AI citation producer independently.")
report_app = typer.Typer(help="Generate and inspect the detailed client report.")
app.add_typer(db_app, name="db")
app.add_typer(citations_app, name="citations")
app.add_typer(report_app, name="report")


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
            "status": result.combined_bundle.status,
            "parent_run_id": result.parent_run_id,
            "citation_bundle_path": result.citation_bundle_path,
            "recon_bundle_path": result.recon_bundle_path,
            "combined_bundle_path": result.combined_bundle_path,
            "combined_bundle_id": result.combined_bundle.bundle_id,
            "checksum": result.combined_bundle.checksum,
        }
    )


def _report_result(result: Any) -> dict[str, Any]:
    snapshot = result.snapshot
    return {
        "status": snapshot.status,
        "client_id": snapshot.client.client_id,
        "company": snapshot.client.canonical_name,
        "parent_run_id": snapshot.parent_run_id,
        "report_id": snapshot.report_id,
        "database_report_id": result.database_report_id,
        "report_type": "detailed_client",
        "config_hash": snapshot.config.report_config_hash,
        "snapshot_checksum": snapshot.checksum,
        "source_bundle_ids": snapshot.source_bundle_ids,
        "source_bundle_checksums": snapshot.source_bundle_checksums,
        "decision_card_count": len(snapshot.decision_cards),
        "action_count": len(snapshot.consolidated_actions),
        "data_quality_flags": snapshot.data_quality_flags,
        "limitations": snapshot.limitations,
        "artifacts": {
            record.artifact_type: {
                "path": record.path,
                "sha256": record.sha256,
                "byte_size": record.byte_size,
            }
            for record in result.manifest.artifacts
        },
        "latest_copies_refreshed": bool(result.latest_paths),
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
    allow_partial: Annotated[
        bool | None,
        typer.Option("--allow-partial/--no-allow-partial", help="Allow partial publication."),
    ] = None,
    refresh_data: Annotated[
        bool,
        typer.Option(
            "--refresh-data",
            help="Explicitly rerun Citation and Recon before creating the report.",
        ),
    ] = False,
) -> None:
    """Generate a report from persisted evidence; refresh producers only on request."""
    if refresh_data:
        if parent_run_id is not None:
            raise typer.BadParameter("--refresh-data cannot be combined with --parent-run-id.")
        if (company is None) == (client_id is None):
            raise typer.BadParameter(
                "With --refresh-data, provide exactly one of --company or --client-id."
            )
        result = _run(
            lambda: run_final_report_pipeline(
                get_settings(),
                get_aivc_settings(),
                company_name=company,
                client_id=client_id,
                config_path=config,
                allow_partial=allow_partial,
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
            lambda: render_historical_report(
                get_settings(),
                parent_run_id=parent_run_id,
                config_path=config,
                allow_partial=allow_partial,
            )
        )
    _print(_report_result(result))


@report_app.command("render")
def report_render(
    parent_run_id: Annotated[UUID, typer.Option("--parent-run-id")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
    allow_partial: Annotated[
        bool | None,
        typer.Option("--allow-partial/--no-allow-partial"),
    ] = None,
) -> None:
    """Rerender one exact persisted run without rerunning producers."""
    result = _run(
        lambda: render_historical_report(
            get_settings(),
            parent_run_id=parent_run_id,
            config_path=config,
            allow_partial=allow_partial,
        )
    )
    _print(_report_result(result))


@report_app.command("show")
def report_show(
    parent_run_id: Annotated[UUID, typer.Option("--parent-run-id")],
) -> None:
    """Show concise metadata for a persisted final report."""
    snapshot = _run(
        lambda: load_final_report_by_parent(
            get_settings(),
            parent_run_id,
            profile="detailed",
            audience="client",
        )
    )
    if snapshot is None:
        raise typer.BadParameter("No persisted report matched that parent run and profile.")
    _print(
        {
            "status": snapshot.status,
            "report_id": snapshot.report_id,
            "parent_run_id": snapshot.parent_run_id,
            "client": snapshot.client.model_dump(mode="json"),
            "report_type": "detailed_client",
            "checksum": snapshot.checksum,
            "decision_card_count": len(snapshot.decision_cards),
            "action_count": len(snapshot.consolidated_actions),
            "data_quality_flags": snapshot.data_quality_flags,
        }
    )


@report_app.command("validate")
def report_validate(
    path: Annotated[Path, typer.Option("--path", help="Final-report JSON path.")],
) -> None:
    """Validate the schema and checksum of a local final report."""
    snapshot = _run(lambda: validate_final_report_file(path))
    _print(
        {
            "valid": True,
            "report_id": snapshot.report_id,
            "parent_run_id": snapshot.parent_run_id,
            "checksum": snapshot.checksum,
        }
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
