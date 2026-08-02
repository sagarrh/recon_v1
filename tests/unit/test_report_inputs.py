from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from ai_visibility.config.settings import Settings
from aivc.contracts.models import AnalysisPeriod, ClientIdentity
from aivc.database.recon_reporting import load_recon_reporting_payload, recon_report_sql
from aivc.reporting.artifacts import write_report_inputs
from aivc.reporting.client_presentation import build_client_presentation
from aivc.reporting.config import load_report_config
from aivc.reporting.context import build_report_input
from aivc.reporting.models import (
    ClientPresentation,
    FinalReportSnapshot,
    FinalReportStatus,
    ReconRecommendationView,
    ReconReportingPayload,
    ReportConfigMetadata,
    SovCompanyPosition,
    TopicPerformance,
)
from aivc.reporting.snapshot import _query_details, _recon_views, _topic_summary

CLIENT_ID = UUID("b88e87f3-0aa5-4da9-be48-2807b12d5a91")
PARENT_ID = UUID("fb279a6c-f285-4c8a-afce-78469c9455e1")


def _recon_payload() -> dict[str, object]:
    return {
        "client": {
            "client_id": str(CLIENT_ID),
            "client_name": "Aprio",
            "competitors": ["Competitor"],
        },
        "clusters": [{"cluster_id": "tax", "cluster_name": "Tax advisory"}],
        "sov": {
            "market_snapshot": [
                {
                    "cluster_id": "tax",
                    "cluster_name": "Tax advisory",
                    "week_date": "2026-07-27",
                    "company_name": "Competitor",
                    "sov": 15.0,
                    "rank": 1,
                    "is_client": False,
                },
                {
                    "cluster_id": "tax",
                    "cluster_name": "Tax advisory",
                    "week_date": "2026-07-27",
                    "company_name": "Aprio",
                    "sov": 7.5,
                    "rank": 2,
                    "is_client": True,
                },
            ],
            "history": [],
        },
        "signals": [
            {
                "id": "watch",
                "cluster_id": "tax",
                "cluster_name": "Tax advisory",
                "triage_severity": "WATCH",
                "primary_competitor": "Competitor",
                "primary_competitor_delta_pp": 2.0,
            },
            {"id": "noise", "triage_severity": "NOISE", "noise": True},
        ],
        "executive_summary": "A measured competitive movement occurred.",
        "recommendations": [
            {
                "id": "recommendation-watch",
                "cluster_id": "tax",
                "cluster_name": "Tax advisory",
                "competitor_name": "Competitor",
                "triage_severity": "WATCH",
                "summary": "Review the measured movement.",
                "action_bullets": ["Audit the affected content."],
            }
        ],
        "run_history": [{"run_id": "run-1", "status": "completed"}],
    }


def _snapshot() -> FinalReportSnapshot:
    config = load_report_config()
    return FinalReportSnapshot(
        report_id="report-test",
        idempotency_key="idempotency-test",
        parent_run_id=PARENT_ID,
        client=ClientIdentity(client_id=CLIENT_ID, canonical_name="Aprio"),
        config=ReportConfigMetadata(
            config_version=config.config.config_version,
            report_config_hash=config.config_hash,
            source=config.source,
            effective_profile=config.profile_settings,
        ),
        source_bundle_ids=["citation", "recon"],
        source_bundle_checksums={"citation": "a" * 64, "recon": "b" * 64},
        analysis_period=AnalysisPeriod(
            start=datetime(2026, 7, 1, tzinfo=UTC),
            end=datetime(2026, 7, 31, tzinfo=UTC),
        ),
        status=FinalReportStatus.complete,
        headline="Measured input",
        executive_summary="Only validated evidence is included.",
        client_presentation=ClientPresentation(
            executive_narrative="Measured executive narrative.",
            main_implication_title="Maintain the baseline",
            main_implication="Continue monitoring validated evidence.",
            strategic_conclusion="Measure progress in the next cycle.",
        ),
    ).sealed()


def test_configuration_has_one_detailed_input_shape() -> None:
    config = load_report_config()
    assert config.config.config_version == "1.3"
    assert config.profile_settings.include_query_details is True
    assert config.profile_settings.include_full_sov_tables is True


def test_packaged_recon_query_is_parameterized_and_read_only() -> None:
    sql = recon_report_sql()
    assert sql.count("%s") == 3
    assert "with\nparams as" in sql.casefold()
    for mutation in ("insert ", "update ", "delete ", "alter ", "drop "):
        assert mutation not in sql.casefold()


def test_recon_repository_sets_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[tuple[str, object]] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: object = None) -> Cursor:
            statements.append((sql, params))
            return self

        def fetchone(self) -> dict[str, object]:
            return {"client_facing_recon_report": _recon_payload()}

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr("aivc.database.recon_reporting.connect", lambda _settings: Connection())
    result = load_recon_reporting_payload(
        Settings(_env_file=None),
        client_id=CLIENT_ID,
        report_week=datetime(2026, 7, 27, tzinfo=UTC).date(),
        history_weeks=16,
    )
    assert result == _recon_payload()
    assert statements[0] == ("set transaction read only", None)


def test_recon_views_filter_noise_and_keep_current_topics() -> None:
    recon = ReconReportingPayload.model_validate(_recon_payload())
    config = load_report_config()
    topics, excluded = _topic_summary([], recon, config)
    signals, recommendations, runs = _recon_views(recon, config)
    assert not excluded
    assert topics[0].current_client_sov == 7.5
    assert [signal.signal_id for signal in signals] == ["watch"]
    assert [item.recommendation_id for item in recommendations] == ["recommendation-watch"]
    assert [run.run_id for run in runs] == ["run-1"]


def test_detailed_input_includes_query_details() -> None:
    citation_report = {
        "query_intelligence": [
            {
                "monitor_query_key": "query-key",
                "query": "Which accounting firm supports growth companies?",
                "provider": "openai",
                "current_visibility": 0.5,
                "trend": "up",
                "run_count": 3,
            }
        ]
    }
    assert len(_query_details(citation_report, load_report_config())) == 1


def test_compact_inputs_are_sealed_atomic_and_written_separately(tmp_path: Path) -> None:
    snapshot = _snapshot()
    report_input = build_report_input(snapshot)
    report_input.verify_checksum()
    manifest, run_paths, latest_paths = write_report_inputs(tmp_path, snapshot, report_input)
    assert {item.artifact_type for item in manifest.artifacts} == {
        "citation_input",
        "recon_input",
        "report_input",
    }
    assert not list(tmp_path.rglob("*.tmp"))
    assert (
        json.loads(run_paths["citation_input"].read_text(encoding="utf-8"))["source_bundle_id"]
        == "citation"
    )
    assert (
        json.loads(run_paths["recon_input"].read_text(encoding="utf-8"))["source_bundle_id"]
        == "recon"
    )
    assert (
        json.loads(run_paths["report_input"].read_text(encoding="utf-8"))["checksum"]
        == report_input.checksum
    )
    assert latest_paths["report_input"].name == "report-input-snapshot.json"


def test_client_presentation_keeps_only_tracked_market_positions() -> None:
    topic = TopicPerformance(
        cluster_id="tax",
        cluster_label="Tax advisory",
        current_client_sov=7.5,
        client_rank=3,
        market_size=20,
        leader_name="Competitor",
        leader_sov=15.0,
        gap_to_leader=7.5,
        positions=[
            SovCompanyPosition(company_name="Competitor", sov=15.0, rank=1, is_tracked=True),
            SovCompanyPosition(
                company_name="Aprio", sov=7.5, rank=3, is_client=True, is_tracked=True
            ),
            SovCompanyPosition(company_name="Untracked", sov=5.0, rank=4),
        ],
    )
    recommendation = ReconRecommendationView(
        recommendation_id="rec-1",
        cluster_id="tax",
        cluster_name="Tax advisory",
        competitor="Competitor",
        priority="high",
        confidence="medium",
        summary="Competitor has the strongest measured position.",
        actions=["Publish authoritative proof."],
    )
    presentation = build_client_presentation(
        client_name="Aprio",
        topics=[topic],
        cards=[],
        recommendations=[recommendation],
        signals=[],
    )
    assert [item.company_name for item in presentation.topics[0].tracked_positions] == [
        "Competitor",
        "Aprio",
    ]


def test_checked_in_migration_and_config_copies_are_identical() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("0005_aivc_final_reports.sql", "0006_aivc_report_audience.sql"):
        assert (root / "migrations" / name).read_bytes() == (
            root / "src/ai_visibility/resources/migrations" / name
        ).read_bytes()
    assert (root / "config/reporting.toml").read_bytes() == (
        root / "src/aivc/resources/config/reporting.toml"
    ).read_bytes()
