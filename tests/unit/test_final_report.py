from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from ai_visibility.config.settings import Settings
from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    ClientIdentity,
    ProducerIdentity,
    SignalBundle,
)
from aivc.database.final_reports import _snapshot_from_row
from aivc.database.recon_reporting import (
    load_recon_reporting_payload,
    recon_report_sql,
)
from aivc.reporting.artifacts import refresh_latest_artifacts, write_final_report_artifacts
from aivc.reporting.config import ReportProfile, load_report_config
from aivc.reporting.models import (
    FinalReportSnapshot,
    FinalReportStatus,
    ReconReportingPayload,
    ReportConfigMetadata,
)
from aivc.reporting.quality import assess_publication
from aivc.reporting.renderers import render_html, render_json, render_markdown
from aivc.reporting.snapshot import _query_details, _recon_views, _topic_summary
from aivc.reporting.validation import validate_final_report_file

CLIENT_ID = UUID("b88e87f3-0aa5-4da9-be48-2807b12d5a91")
PARENT_ID = UUID("fb279a6c-f285-4c8a-afce-78469c9455e1")


def _recon_reporting_payload() -> dict[str, object]:
    competitors = [f"Competitor {number}" for number in range(1, 10)]
    market = [
        {
            "cluster_id": "tax",
            "cluster_name": "Tax advisory",
            "week_date": "2026-07-27",
            "company_name": name,
            "sov": float(25 - number),
            "rank": number,
            "is_client": False,
        }
        for number, name in enumerate(competitors, start=1)
    ]
    market.append(
        {
            "cluster_id": "tax",
            "cluster_name": "Tax advisory",
            "week_date": "2026-07-27",
            "company_name": "Aprio",
            "sov": 7.5,
            "rank": 10,
            "is_client": True,
        }
    )
    market.append(
        {
            "cluster_id": "bad",
            "cluster_name": "Unrelated topic",
            "week_date": "2026-07-27",
            "company_name": "Aprio",
            "sov": 99.0,
            "rank": 1,
            "is_client": True,
        }
    )
    return {
        "client": {
            "client_id": str(CLIENT_ID),
            "client_name": "Aprio",
            "competitors": ["Competitor 9"],
        },
        "clusters": [
            {"cluster_id": "tax", "cluster_name": "Tax advisory"},
            {"cluster_id": "bad", "cluster_name": "Unrelated topic\n"},
        ],
        "sov": {
            "market_snapshot": market,
            "history": [
                {
                    "cluster_id": "tax",
                    "cluster_name": "Tax advisory",
                    "week_date": "2026-07-27",
                    "client_sov_this_week": 7.5,
                    "competitor_name": "Competitor 1",
                    "current_sov": 24.0,
                    "sov_delta_pp": 2.0,
                    "answers_analyzed": 40,
                    "platforms_analyzed": ["openai"],
                }
            ],
        },
        "signals": [
            {
                "id": "watch",
                "cluster_id": "tax",
                "cluster_name": "Tax advisory",
                "triage_severity": "WATCH",
                "primary_competitor": "Competitor 1",
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
                "competitor_name": "Competitor 1",
                "triage_severity": "WATCH",
                "summary": "Review the competitor movement.",
                "action_bullets": ["Audit the affected content."],
            },
            {
                "id": "recommendation-noise",
                "triage_severity": "NOISE",
                "summary": "Do not publish this.",
            },
        ],
        "run_history": [
            {"run_id": "run-1", "status": "completed", "triggers_fired": 1}
        ],
    }


def _snapshot(profile: ReportProfile = ReportProfile.decision) -> FinalReportSnapshot:
    config = load_report_config(profile=profile)
    return FinalReportSnapshot(
        report_id="report-test",
        idempotency_key="idempotency-test",
        parent_run_id=PARENT_ID,
        client=ClientIdentity(
            client_id=CLIENT_ID,
            canonical_name="Aprio <script>alert(1)</script>",
            aliases=["Aprio"],
        ),
        config=ReportConfigMetadata(
            config_version=config.config.config_version,
            report_profile=config.profile,
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
        headline="Measured report",
        executive_summary="Only validated evidence is included.",
    ).sealed()


def test_config_profile_override_and_detailed_is_broader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Environment selects detailed, while an explicit CLI-style value wins.
    monkeypatch.setenv("AIVC_REPORT_PROFILE", "detailed")
    assert load_report_config().profile is ReportProfile.detailed
    explicit = load_report_config(profile=ReportProfile.decision)
    assert explicit.profile is ReportProfile.decision
    detailed = load_report_config(profile=ReportProfile.detailed)
    assert (
        detailed.profile_settings.max_decision_cards
        >= explicit.profile_settings.max_decision_cards
    )


def test_packaged_recon_query_is_parameterized_and_read_only() -> None:
    sql = recon_report_sql()
    assert sql.count("%s") == 3
    assert "with\nparams as" in sql.casefold()
    for mutation in ("insert ", "update ", "delete ", "alter ", "drop "):
        assert mutation not in sql.casefold()


def test_recon_repository_sets_read_only_and_uses_exact_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _recon_reporting_payload()
    statements: list[tuple[str, object]] = []

    class FakeCursor:
        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: object = None) -> FakeCursor:
            statements.append((sql, params))
            return self

        def fetchone(self) -> dict[str, object]:
            return {"client_facing_recon_report": payload}

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self) -> FakeCursor:
            return FakeCursor()

    monkeypatch.setattr(
        "aivc.database.recon_reporting.connect", lambda _settings: FakeConnection()
    )
    report_week = datetime(2026, 7, 27, tzinfo=UTC).date()
    result = load_recon_reporting_payload(
        Settings(_env_file=None),
        client_id=CLIENT_ID,
        report_week=report_week,
        history_weeks=16,
    )

    assert result == payload
    assert statements[0] == ("set transaction read only", None)
    assert statements[1][1] == (CLIENT_ID, report_week, 16)


def test_recon_payload_is_retained_while_views_apply_quality_gates() -> None:
    source = _recon_reporting_payload()
    recon = ReconReportingPayload.model_validate(source)
    assert recon.model_dump(mode="json") == source

    decision = load_report_config(profile=ReportProfile.decision)
    detailed = load_report_config(profile=ReportProfile.detailed)
    decision_topics, decision_excluded = _topic_summary([], recon, decision)
    detailed_topics, detailed_excluded = _topic_summary([], recon, detailed)

    assert [topic.cluster_label for topic in decision_topics] == ["Tax advisory"]
    assert [topic.cluster_name for topic in decision_excluded] == ["Unrelated topic"]
    assert detailed_excluded == decision_excluded
    assert len(decision_topics[0].positions) < len(detailed_topics[0].positions)
    assert decision_topics[0].history

    signals, recommendations, runs = _recon_views(recon, detailed)
    assert [signal.signal_id for signal in signals] == ["watch"]
    assert [item.recommendation_id for item in recommendations] == [
        "recommendation-watch"
    ]
    assert [run.run_id for run in runs] == ["run-1"]


def test_query_details_are_profile_controlled() -> None:
    citation_report = {
        "query_intelligence": [
            {
                "monitor_query_key": "query-key",
                "query": "Which accounting firm supports growth companies?",
                "provider": "openai",
                "cluster_id": "tax",
                "cluster_name": "Tax advisory",
                "current_visibility": 0.5,
                "latest_delta": 0.1,
                "trend": "up",
                "run_count": 3,
            }
        ]
    }
    decision = load_report_config(profile=ReportProfile.decision)
    detailed = load_report_config(profile=ReportProfile.detailed)
    assert _query_details(citation_report, decision) == []
    assert len(_query_details(citation_report, detailed)) == 1


def test_renderers_validate_checksum_escape_html_and_keep_json_exact() -> None:
    snapshot = _snapshot()
    assert "<script>" in render_markdown(snapshot)
    assert "&lt;script&gt;" in render_html(snapshot)
    assert "<script>alert" not in render_html(snapshot)
    payload = json.loads(render_json(snapshot))
    assert payload["checksum"] == snapshot.checksum
    assert payload["client"]["canonical_name"].startswith("Aprio")


def test_artifacts_are_atomic_manifested_and_validated(tmp_path: Path) -> None:
    snapshot = _snapshot()
    manifest, paths = write_final_report_artifacts(
        tmp_path, snapshot, write_latest_copies=False
    )
    assert {item.artifact_type for item in manifest.artifacts} == {
        "json",
        "markdown",
        "html",
    }
    assert not list(tmp_path.rglob("*.tmp"))
    validated = validate_final_report_file(paths["json"])
    assert validated.checksum == snapshot.checksum
    latest = refresh_latest_artifacts(tmp_path, snapshot)
    assert set(latest) == {"json", "md", "html"}


def test_schema_10_snapshot_remains_readable_for_historical_upgrade() -> None:
    payload = _snapshot().model_dump(mode="json")
    payload["schema_version"] = "1.0"
    for field in (
        "query_details",
        "recon_signals",
        "recon_recommendations",
        "recon_run_history",
        "excluded_topics",
        "recon_reporting",
    ):
        payload.pop(field)
    canonical = {
        key: value
        for key, value in payload.items()
        if key not in {"checksum", "generated_at"}
    }
    payload["checksum"] = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    loaded = _snapshot_from_row({"structured_snapshot": payload})
    assert loaded is not None
    assert loaded.schema_version == "1.0"
    assert loaded.recon_reporting is None


def test_checked_in_migration_copies_are_identical() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "migrations/0005_aivc_final_reports.sql").read_bytes() == (
        root / "src/ai_visibility/resources/migrations/0005_aivc_final_reports.sql"
    ).read_bytes()


def test_recon_recommendation_requires_a_publishable_recon_signal() -> None:
    client = ClientIdentity(client_id=CLIENT_ID, canonical_name="Aprio")

    def bundle(name: str, bundle_id: str) -> SignalBundle:
        return SignalBundle(
            bundle_id=bundle_id,
            created_at=datetime.now(UTC),
            producer=ProducerIdentity(name=name, version="1", run_id=bundle_id),
            client=client,
            analysis_period=AnalysisPeriod(),
            status=BundleStatus.complete,
        ).sealed()

    citation = bundle("ai_visibility", "citation")
    recon = bundle("scout", "recon").model_copy(
        update={
            "recommendations": [
                {
                    "cluster_id": "noise-cluster",
                    "competitor_name": "BDO",
                    "validation_status": "ok",
                    "action_bullets": ["Do not publish this noise-derived action."],
                }
            ]
        }
    ).sealed()
    combined = bundle("aivc_combined", "combined").model_copy(
        update={"source_bundle_ids": [citation.bundle_id, recon.bundle_id]}
    ).sealed()
    publication = assess_publication(citation, recon, combined)
    assert publication.recon_signals == ()
    assert publication.recommendations == ()
