from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any

import typer

from ai_visibility.config.settings import get_settings
from ai_visibility.database.validation import check_database
from aivc.config import get_aivc_settings
from aivc.database import audit_shared_schema
from aivc.orchestration import run_integrated_pipeline
from aivc.producers import generate_citation_bundle

app = typer.Typer(
    name="aivc",
    help="Run the integrated AIVC intelligence backend.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Validate shared database configuration.")
citations_app = typer.Typer(help="Run the AI citation producer independently.")
app.add_typer(db_app, name="db")
app.add_typer(citations_app, name="citations")


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
    result, bundle_path, bundle = _run(
        lambda: generate_citation_bundle(get_settings(), company)
    )
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
    company: Annotated[str, typer.Option("--company", help="Exact client company name.")],
) -> None:
    """Run citation, Recon, persistence, delivery, and bundle composition."""
    result = _run(
        lambda: run_integrated_pipeline(
            get_settings(), get_aivc_settings(), company
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
