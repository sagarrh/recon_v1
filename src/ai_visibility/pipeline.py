from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import structlog

from ai_visibility.companies.resolver import ResolvedClient, resolve_client, resolve_client_by_id
from ai_visibility.config.settings import Settings
from ai_visibility.database.migrations import apply_migrations
from ai_visibility.database.repository import (
    load_page_intelligence,
    load_raw_runs,
    persist_normalized_run,
)
from ai_visibility.database.validation import check_database
from ai_visibility.jobs.queue import enqueue_report_pages
from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.normalization.parsing import as_dict
from ai_visibility.normalization.runs import normalize_run
from ai_visibility.reports.builder import build_report
from ai_visibility.reports.persistence import persist_report, write_report_files
from ai_visibility.reports.validation import validate_report_schema

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class GenerationResult:
    report_id: UUID
    json_path: Path
    markdown_path: Path
    valid_run_count: int
    invalid_run_count: int
    queued_page_count: int
    report: dict[str, object]
    normalized_runs: tuple[NormalizedRun, ...]


def generate_company_report(
    settings: Settings,
    company_name: str | None = None,
    *,
    client_id: UUID | None = None,
) -> GenerationResult:
    if (company_name is None) == (client_id is None):
        raise ValueError("Provide exactly one of company_name or client_id.")
    logger.info(
        "report_generation_started", company=company_name, client_id=str(client_id or "")
    )
    # Fail before any writes if the source is missing or incompatible.
    check_database(settings)
    apply_migrations(settings)
    client: ResolvedClient = (
        resolve_client_by_id(settings, client_id)
        if client_id is not None
        else resolve_client(settings, str(company_name))
    )
    raw_runs = load_raw_runs(settings, client.client_id)
    company_names = {client.canonical_name}
    for raw in raw_runs:
        company_names.update(str(name) for name in as_dict(raw.companies_data))
    normalized = [normalize_run(raw, company_names) for raw in raw_runs]
    for run in normalized:
        persist_normalized_run(
            settings,
            run,
            client_company_name=client.canonical_name,
        )
    page_evidence = json.loads(
        json.dumps(
            load_page_intelligence(
                settings,
                client.client_id,
                client.canonical_name,
            ),
            default=str,
        )
    )
    report_model = build_report(
        normalized,
        client.canonical_name,
        client_id=str(client.client_id),
        page_evidence=page_evidence,
        configured_official_domains=list(client.official_domains),
    )
    report = report_model.model_dump(mode="json")
    try:
        queued = enqueue_report_pages(settings, report)
    except Exception as exc:
        queued = 0
        report["data_quality_flags"] = sorted(
            {
                *report.get("data_quality_flags", []),
                "page_queue_unavailable",
            }
        )
        report["page_intelligence"]["queue_error"] = (
            "Page enrichment queue was unavailable; observation reporting completed."
        )
        logger.warning(
            "page_queue_unavailable",
            client_id=str(client.client_id),
            company=client.canonical_name,
            error_type=type(exc).__name__,
        )
    validate_report_schema(report)
    report_id = persist_report(settings, report)
    json_path, markdown_path = write_report_files(settings, client.canonical_name, report)
    logger.info(
        "report_generation_completed",
        client_id=str(client.client_id),
        company=client.canonical_name,
        report_id=str(report_id),
        valid_run_count=sum(run.is_valid for run in normalized),
        invalid_run_count=sum(not run.is_valid for run in normalized),
        queued_page_count=queued,
    )
    return GenerationResult(
        report_id=report_id,
        json_path=json_path,
        markdown_path=markdown_path,
        valid_run_count=sum(run.is_valid for run in normalized),
        invalid_run_count=sum(not run.is_valid for run in normalized),
        queued_page_count=queued,
        report=report,
        normalized_runs=tuple(normalized),
    )
