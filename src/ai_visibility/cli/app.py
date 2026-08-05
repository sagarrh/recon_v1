from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

import httpx
import psycopg
import typer

from ai_visibility.companies.resolver import resolve_client
from ai_visibility.config.logging import configure_logging
from ai_visibility.config.settings import Settings, get_settings
from ai_visibility.database.migrations import apply_migrations
from ai_visibility.database.repository import (
    list_client_ids,
    load_raw_run,
    load_raw_runs,
    persist_normalized_run,
    processing_status,
)
from ai_visibility.database.validation import check_database
from ai_visibility.jobs.queue import (
    list_failed_jobs,
    process_page_jobs,
    retry_failed_jobs,
)
from ai_visibility.normalization.parsing import as_dict
from ai_visibility.normalization.runs import normalize_run
from ai_visibility.pipeline import generate_company_report
from ai_visibility.reports.markdown import render_markdown
from ai_visibility.reports.persistence import (
    load_latest_persisted_report,
    load_local_report,
)
from ai_visibility.scraping.fetcher import fetch_page

app = typer.Typer(
    name="ai-visibility",
    help="Generate deterministic company intelligence from immutable AI monitoring data.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Check and migrate the direct PostgreSQL database.")
runs_app = typer.Typer(help="Normalize and inspect monitoring history.")
pages_app = typer.Typer(help="Fetch and process selectively queued citation pages.")
jobs_app = typer.Typer(help="Inspect and retry persisted processing jobs.")
report_app = typer.Typer(help="Generate and display Company Intelligence Reports.")
app.add_typer(db_app, name="db")
app.add_typer(runs_app, name="runs")
app.add_typer(pages_app, name="pages")
app.add_typer(jobs_app, name="jobs")
app.add_typer(report_app, name="report")

class ReportFormat(StrEnum):
    json = "json"
    markdown = "markdown"


def _settings() -> Settings:
    return get_settings()


def _print_json(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, default=str, ensure_ascii=False))


def _run(action: Any) -> Any:
    try:
        return action()
    except (
        RuntimeError,
        ValueError,
        LookupError,
        psycopg.Error,
        httpx.HTTPError,
    ) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


@app.callback()
def callback(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable debug-level structured logs.")
    ] = False,
) -> None:
    configure_logging(verbose)


@db_app.command("check")
def db_check() -> None:
    """Validate the connection and immutable source-table shape."""
    result = _run(lambda: check_database(_settings()))
    _print_json(result)


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply append-only analytical migrations."""
    applied = _run(lambda: apply_migrations(_settings()))
    _print_json({"applied": applied, "applied_count": len(applied)})


def _company_names(raw_runs: list[Any], explicit_company: str | None) -> set[str]:
    names = {explicit_company} if explicit_company else set()
    for raw in raw_runs:
        names.update(str(name) for name in as_dict(raw.companies_data))
    return {name for name in names if name}


@runs_app.command("normalize")
def runs_normalize(
    run_id: Annotated[UUID, typer.Option("--run-id", help="Raw monitoring run UUID.")],
    company: Annotated[
        str | None,
        typer.Option("--company", help="Client company name; inferred if omitted."),
    ] = None,
) -> None:
    """Normalize one immutable raw run idempotently."""

    def action() -> dict[str, Any]:
        settings = _settings()
        check_database(settings)
        apply_migrations(settings)
        raw = load_raw_run(settings, run_id)
        names = _company_names([raw], company)
        client_name = company
        normalized = normalize_run(
            raw,
            names | ({client_name} if client_name else set()),
        )
        persist_normalized_run(settings, normalized, client_company_name=client_name)
        return {
            "run_id": str(run_id),
            "valid": normalized.is_valid,
            "answer_count": len(normalized.answers),
            "data_quality_flags": normalized.data_quality_flags,
            "client_relationship_inferred": client_name is not None,
        }

    _print_json(_run(action))


@runs_app.command("backfill")
def runs_backfill(
    company: Annotated[
        str | None,
        typer.Option("--company", help="Limit to a resolved client company."),
    ] = None,
) -> None:
    """Normalize complete raw history chronologically and idempotently."""

    def action() -> dict[str, Any]:
        settings = _settings()
        check_database(settings)
        apply_migrations(settings)
        client_ids = (
            [resolve_client(settings, company).client_id] if company else list_client_ids(settings)
        )
        processed = valid = invalid = 0
        for client_id in client_ids:
            raw_runs = load_raw_runs(settings, client_id)
            names = _company_names(raw_runs, company)
            client_name = company
            for raw in raw_runs:
                normalized = normalize_run(
                    raw,
                    names | ({client_name} if client_name else set()),
                )
                persist_normalized_run(
                    settings,
                    normalized,
                    client_company_name=client_name,
                )
                processed += 1
                valid += int(normalized.is_valid)
                invalid += int(not normalized.is_valid)
        return {
            "processed": processed,
            "valid": valid,
            "invalid": invalid,
            "client_count": len(client_ids),
            "client_relationships_inferred": company is not None,
        }

    _print_json(_run(action))


@runs_app.command("status")
def runs_status() -> None:
    """Show normalized run checkpoints."""
    _print_json(_run(lambda: processing_status(_settings())))


@pages_app.command("fetch")
def pages_fetch(
    url: Annotated[str, typer.Option("--url", help="Public HTML or PDF URL.")],
) -> None:
    """Securely fetch and extract one page without persisting it."""

    def action() -> dict[str, Any]:
        result = fetch_page(_settings(), url)
        return {
            "requested_url": result.requested_url,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "content_type": result.content_type,
            "title": result.title,
            "main_text_hash": result.main_text_hash,
            "text_length": len(result.main_text),
            "fetch_method": result.fetch_method,
            "extraction_quality": result.extraction_quality,
            "fetch_metadata": result.fetch_metadata,
        }

    _print_json(_run(action))


@pages_app.command("process")
def pages_process(
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 20,
) -> None:
    """Process ready page jobs with bounded retries."""
    _print_json(_run(lambda: process_page_jobs(_settings(), limit=limit)))


@jobs_app.command("failed")
def jobs_failed(
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 100,
) -> None:
    """List failed page jobs."""
    _print_json(_run(lambda: list_failed_jobs(_settings(), limit=limit)))


@jobs_app.command("retry")
def jobs_retry() -> None:
    """Move failed page jobs back to the retry queue."""
    count = _run(lambda: retry_failed_jobs(_settings()))
    _print_json({"retried": count})


@report_app.command("generate")
def report_generate(
    company: Annotated[str, typer.Option("--company", help="Client company name.")],
) -> None:
    """Generate, persist, validate, and write a complete report."""

    def action() -> dict[str, Any]:
        result = generate_company_report(_settings(), company)
        return {
            "status": "complete",
            "report_id": str(result.report_id),
            "valid_run_count": result.valid_run_count,
            "invalid_run_count": result.invalid_run_count,
            "queued_page_count": result.queued_page_count,
            "json_path": str(result.json_path),
            "markdown_path": str(result.markdown_path),
        }

    _print_json(_run(action))


@report_app.command("show")
def report_show(
    company: Annotated[str, typer.Option("--company", help="Client company name.")],
    output_format: Annotated[
        ReportFormat, typer.Option("--format", help="Output representation.")
    ] = ReportFormat.json,
) -> None:
    """Display the latest local or persisted report."""

    def action() -> str:
        settings = _settings()
        report = load_local_report(settings, company)
        if settings.database_url is not None:
            try:
                persisted = load_latest_persisted_report(settings, company)
            except psycopg.Error:
                persisted = None
            if persisted is not None:
                report = persisted
        if report is None:
            raise LookupError(f"No completed report is available for {company!r}.")
        if output_format is ReportFormat.markdown:
            return render_markdown(report)
        return json.dumps(report, indent=2, ensure_ascii=False)

    typer.echo(_run(action))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
