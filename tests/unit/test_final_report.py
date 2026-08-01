from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    ClientIdentity,
    ProducerIdentity,
    SignalBundle,
)
from aivc.reporting.artifacts import refresh_latest_artifacts, write_final_report_artifacts
from aivc.reporting.config import ReportProfile, load_report_config
from aivc.reporting.models import (
    FinalReportSnapshot,
    FinalReportStatus,
    ReportConfigMetadata,
)
from aivc.reporting.quality import assess_publication
from aivc.reporting.renderers import render_html, render_json, render_markdown
from aivc.reporting.validation import validate_final_report_file

CLIENT_ID = UUID("b88e87f3-0aa5-4da9-be48-2807b12d5a91")
PARENT_ID = UUID("fb279a6c-f285-4c8a-afce-78469c9455e1")


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
