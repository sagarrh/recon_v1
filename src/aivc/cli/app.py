from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer

from ai_visibility.companies.resolver import resolve_client, resolve_client_by_id
from ai_visibility.config.settings import get_settings
from ai_visibility.database.validation import check_database
from ai_visibility.measurement.persistence import load_outcome_summary
from ai_visibility.measurement.runner import evaluate_plans, plan_measurements
from aivc.config import get_aivc_settings
from aivc.database import audit_shared_schema, check_source_health, summarize_source_health
from aivc.database.evidence import resolve_latest_evidence_parent
from aivc.database.executions import (
    list_executions,
    load_execution,
    record_execution,
    verify_execution,
)
from aivc.database.source_health import DEFAULT_MAX_LAG_DAYS
from aivc.orchestration import (
    prepare_fresh_report_inputs,
    prepare_historical_report_inputs,
    run_integrated_pipeline,
)
from aivc.producers import generate_citation_bundle
from aivc.reporting.funnel import load_execution_funnel
from aivc.reporting.outcome_cards import load_outcome_cards

app = typer.Typer(
    name="aivc",
    help="Run the integrated AIVC intelligence backend.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Validate shared database configuration.")
citations_app = typer.Typer(help="Run the AI citation producer independently.")
report_app = typer.Typer(help="Prepare compact Citation + Recon inputs for a client report.")
execution_app = typer.Typer(
    help="Record when a recommendation was implemented, so its outcome can be measured."
)
measurement_app = typer.Typer(
    help="Measure what followed an executed recommendation in Google Search Console."
)
app.add_typer(db_app, name="db")
app.add_typer(citations_app, name="citations")
app.add_typer(report_app, name="report")
app.add_typer(execution_app, name="execution")
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


@db_app.command("health")
def db_health(
    max_lag_days: Annotated[
        int,
        typer.Option("--max-lag-days", help="Days a source may fall behind before it is stale."),
    ] = DEFAULT_MAX_LAG_DAYS,
) -> None:
    """Check whether the mirrored sources are still actually arriving.

    Separate from `db audit`, which checks structure. A schema can be perfect while a source has
    silently stopped advancing — which is exactly how ai_monitoring reported success for months
    while writing nothing. Exits non-zero when any source is unhealthy, so it can gate a cron."""

    def action() -> dict[str, Any]:
        sources = check_source_health(get_settings(), max_lag_days=max_lag_days)
        return summarize_source_health(sources)

    summary = _run(action)
    _print(summary)
    if not summary["healthy"]:
        raise typer.Exit(code=1)


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


_COMPANY_OPT = typer.Option("--company", help="Exact client company name.")
_CLIENT_ID_OPT = typer.Option("--client-id", help="Client UUID; use when the name is ambiguous.")


def _resolved_client(company: str | None, client_id: UUID | None) -> tuple[Any, frozenset[str]]:
    """Resolve a client by exact name or by id, and return the domains it owns.

    --client-id exists because duplicate client rows make some names genuinely ambiguous, and the
    resolver refuses to guess rather than picking one. The measurement exclusion list deliberately
    does NOT disambiguate here: it is measurement policy, and letting it alter identity resolution
    would mean a measurement setting silently changed which client a citation report was built for.
    """
    if (company is None) == (client_id is None):
        raise ValueError("Provide exactly one of --company or --client-id.")
    settings = get_settings()
    client = (
        resolve_client_by_id(settings, client_id)
        if client_id is not None
        else resolve_client(settings, str(company))
    )
    return client, frozenset(d.casefold() for d in client.official_domains if d)


def _execution_json(execution: Any) -> dict[str, Any]:
    return {
        "subject_type": execution.subject_type,
        "subject_id": execution.subject_id,
        "client_id": str(execution.client_id),
        "status": execution.status,
        "implemented_at": execution.implemented_at,
        "implemented_by": execution.implemented_by,
        "target_pages": execution.target_pages,
        "target_queries": execution.target_queries,
        "action_type": execution.action_type,
        "verification_status": execution.verification_status,
        "measurable": execution.is_measurable,
    }


@execution_app.command("record")
def execution_record(
    recommendation_id: Annotated[
        str, typer.Option("--recommendation-id", help="Recommendation this action implements.")
    ],
    company: Annotated[str | None, _COMPANY_OPT] = None,
    client_id: Annotated[UUID | None, _CLIENT_ID_OPT] = None,
    implemented_at: Annotated[
        datetime | None,
        typer.Option(
            "--implemented-at",
            formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"],
            help="When the change actually shipped. Required for executed/verified.",
        ),
    ] = None,
    by: Annotated[
        str | None, typer.Option("--by", help="Who confirmed it, e.g. cs@example.com.")
    ] = None,
    status: Annotated[
        str, typer.Option("--status", help="proposed|accepted|executed|verified|abandoned.")
    ] = "executed",
    page: Annotated[
        list[str] | None,
        typer.Option("--page", help="Target page actually changed. Repeatable."),
    ] = None,
    query: Annotated[
        list[str] | None, typer.Option("--query", help="Target query. Repeatable.")
    ] = None,
    action_type: Annotated[str, typer.Option("--action-type")] = "other",
    notes: Annotated[str | None, typer.Option("--notes")] = None,
) -> None:
    """Record that a recommendation was implemented.

    The implemented_at timestamp is the measurement anchor: outcome windows are counted from it and
    from nothing else. Recommendations with no execution record are reported as awaiting_execution
    rather than measured, so growth is never credited to an action that never shipped."""

    def action() -> dict[str, Any]:
        settings = get_settings()
        client, _ = _resolved_client(company, client_id)
        execution = record_execution(
            settings,
            subject_id=recommendation_id,
            client_id=client.client_id,
            status=status,
            implemented_at=implemented_at,
            implemented_by=by,
            target_pages=list(page or []),
            target_queries=list(query or []),
            action_type=action_type,
            implementation_notes=notes,
            # The resolver's authority on what this client owns. Pages outside it are refused:
            # target pages scope GA4 measurement, so a third-party URL would attribute
            # someone else's traffic to this client.
            owned_domains=tuple(client.official_domains),
        )
        return _execution_json(execution)

    _print(_run(action))


@execution_app.command("verify")
def execution_verify(
    recommendation_id: Annotated[str, typer.Option("--recommendation-id")],
    verification: Annotated[
        str,
        typer.Option(
            "--verification", help="unverified|snapshot_confirmed|manual_confirmed."
        ),
    ] = "manual_confirmed",
) -> None:
    """Confirm a recorded execution. Never invents an implemented_at that was not supplied."""
    _print(
        _run(
            lambda: _execution_json(
                verify_execution(
                    get_settings(),
                    subject_id=recommendation_id,
                    verification_status=verification,
                )
            )
        )
    )


@execution_app.command("show")
def execution_show(
    recommendation_id: Annotated[str, typer.Option("--recommendation-id")],
) -> None:
    """Show one execution record, or report that none exists."""

    def action() -> dict[str, Any]:
        execution = load_execution(get_settings(), subject_id=recommendation_id)
        if execution is None:
            return {
                "subject_id": recommendation_id,
                "status": "awaiting_execution",
                "measurable": False,
            }
        return _execution_json(execution)

    _print(_run(action))


@execution_app.command("list")
def execution_list(
    company: Annotated[str | None, _COMPANY_OPT] = None,
    client_id: Annotated[UUID | None, _CLIENT_ID_OPT] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
) -> None:
    """List execution records, newest implementation first."""

    def action() -> dict[str, Any]:
        settings = get_settings()
        resolved = client_id
        if company is not None:
            resolved = _resolved_client(company, None)[0].client_id
        executions = list_executions(settings, client_id=resolved, status=status)
        return {
            "count": len(executions),
            "measurable": sum(1 for e in executions if e.is_measurable),
            "executions": [_execution_json(e) for e in executions],
        }

    _print(_run(action))


@measurement_app.command("plan")
def measurement_plan(
    company: Annotated[str | None, _COMPANY_OPT] = None,
    client_id: Annotated[UUID | None, _CLIENT_ID_OPT] = None,
) -> None:
    """Create a measurement plan for every executed recommendation.

    Run this as soon as executions are recorded. The plan fixes the windows and targets BEFORE the
    follow-up window closes, so the comparison cannot later be chosen to suit the answer."""

    def action() -> dict[str, Any]:
        client, domains = _resolved_client(company, client_id)
        reports = plan_measurements(
            get_settings(), client_id=client.client_id, owned_domains=domains
        )
        return {
            "client": client.canonical_name,
            "planned": len(reports),
            "by_status": {
                status: sum(1 for r in reports if r.status == status)
                for status in sorted({r.status for r in reports})
            },
            "plans": [
                {
                    "subject_id": r.subject_id, "status": r.status, "reason": r.reason,
                    "matched_queries": r.matched_queries, "matched_pages": r.matched_pages,
                }
                for r in reports
            ],
        }

    _print(_run(action))


@measurement_app.command("evaluate")
def measurement_evaluate(
    company: Annotated[str | None, _COMPANY_OPT] = None,
    client_id: Annotated[UUID | None, _CLIENT_ID_OPT] = None,
) -> None:
    """Capture both windows and record an outcome for every plan whose window has closed."""

    def action() -> dict[str, Any]:
        client, domains = _resolved_client(company, client_id)
        results = evaluate_plans(
            get_settings(), client_id=client.client_id, owned_domains=domains
        )
        return {
            "client": client.canonical_name,
            "evaluated": len(results),
            "by_classification": {
                name: sum(1 for r in results if r.classification == name)
                for name in sorted({r.classification for r in results})
            },
            "outcomes": [
                {
                    "subject_id": r.subject_id,
                    "classification": r.classification,
                    "confidence": r.confidence,
                    "deltas": r.deltas,
                }
                for r in results
            ],
        }

    _print(_run(action))


@measurement_app.command("status")
def measurement_status(
    company: Annotated[str | None, _COMPANY_OPT] = None,
    client_id: Annotated[UUID | None, _CLIENT_ID_OPT] = None,
) -> None:
    """Show every plan for a client and whatever outcome it has reached."""

    def action() -> dict[str, Any]:
        client, _ = _resolved_client(company, client_id)
        rows = load_outcome_summary(get_settings(), client_id=client.client_id)
        return {
            "client": client.canonical_name,
            "plan_count": len(rows),
            "measured": sum(1 for r in rows if r.get("classification")),
            "plans": rows,
        }

    _print(_run(action))


@measurement_app.command("report")
def measurement_report(
    company: Annotated[str | None, _COMPANY_OPT] = None,
    client_id: Annotated[UUID | None, _CLIENT_ID_OPT] = None,
) -> None:
    """Outcome cards for every executed recommendation, plus the execution funnel.

    Each card reports the funnel one layer at a time. A layer with no source says `unavailable`,
    never zero — "no GA4 connection" and "no conversions" are different facts, and collapsing them
    would let a missing integration read as a failed intervention."""

    def action() -> dict[str, Any]:
        settings = get_settings()
        client, _ = _resolved_client(company, client_id)
        cards = load_outcome_cards(settings, client_id=client.client_id)
        funnel = load_execution_funnel(settings, client_id=client.client_id)
        return {
            "client": client.canonical_name,
            **funnel.as_dict(),
            "cards": [card.as_dict() for card in cards],
        }

    _print(_run(action))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
