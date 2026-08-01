from __future__ import annotations

from typing import Any
from uuid import UUID

from conftest import CLIENT_ID

from ai_visibility.companies.resolver import ResolvedClient
from ai_visibility.config.settings import Settings
from ai_visibility.normalization.models import RawMonitoringRun
from ai_visibility.pipeline import generate_company_report


def test_page_queue_failure_does_not_prevent_report_output(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    import ai_visibility.pipeline as pipeline

    persisted: dict[str, Any] = {}
    monkeypatch.setattr(pipeline, "check_database", lambda settings: {})
    monkeypatch.setattr(pipeline, "apply_migrations", lambda settings: [])
    monkeypatch.setattr(
        pipeline,
        "resolve_client",
        lambda settings, company: ResolvedClient(
            canonical_name="Aprio",
            client_id=CLIENT_ID,
            aliases=("Aprio",),
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "load_raw_runs",
        lambda settings, client_id: list(raw_aprio_pair),
    )
    monkeypatch.setattr(
        pipeline,
        "persist_normalized_run",
        lambda settings, run, client_company_name: None,
    )
    monkeypatch.setattr(
        pipeline,
        "load_page_intelligence",
        lambda settings, client_id, company: [],
    )

    def fail_queue(settings: Settings, report: dict[str, Any]) -> int:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(pipeline, "enqueue_report_pages", fail_queue)

    def persist(settings: Settings, report: dict[str, Any]) -> UUID:
        persisted.update(report)
        return UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    monkeypatch.setattr(pipeline, "persist_report", persist)
    settings = Settings(
        database_url="postgresql://unused",
        report_output_dir=tmp_path,
    )
    result = generate_company_report(settings, "Aprio")
    assert result.queued_page_count == 0
    assert result.json_path.exists()
    assert result.markdown_path.exists()
    assert "page_queue_unavailable" in persisted["data_quality_flags"]
    assert "observation reporting completed" in persisted["page_intelligence"]["queue_error"]
