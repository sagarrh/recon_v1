from __future__ import annotations

from typing import Any

from conftest import CLIENT_ID
from typer.testing import CliRunner

from ai_visibility.cli.app import app
from ai_visibility.config.settings import Settings
from ai_visibility.normalization.models import RawMonitoringRun
from aivc.cli.app import app as aivc_app


def test_unscoped_backfill_does_not_guess_client_company(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
    monkeypatch: Any,
) -> None:
    import ai_visibility.cli.app as cli

    persisted_client_names: list[str | None] = []
    monkeypatch.setattr(
        cli,
        "_settings",
        lambda: Settings(database_url="postgresql://unused"),
    )
    monkeypatch.setattr(cli, "check_database", lambda settings: {})
    monkeypatch.setattr(cli, "apply_migrations", lambda settings: [])
    monkeypatch.setattr(cli, "list_client_ids", lambda settings: [CLIENT_ID])
    monkeypatch.setattr(
        cli,
        "load_raw_runs",
        lambda settings, client_id: [raw_aprio_pair[0]],
    )

    def persist(
        settings: Settings,
        run: Any,
        *,
        client_company_name: str | None,
    ) -> None:
        persisted_client_names.append(client_company_name)

    monkeypatch.setattr(cli, "persist_normalized_run", persist)
    result = CliRunner().invoke(app, ["runs", "backfill"])
    assert result.exit_code == 0, result.output
    assert persisted_client_names == [None]
    assert '"client_relationships_inferred": false' in result.output.casefold()


def test_final_report_cli_has_one_detailed_client_product() -> None:
    result = CliRunner().invoke(aivc_app, ["report", "generate", "--help"])
    assert result.exit_code == 0, result.output
    assert "--profile" not in result.output
    assert "--audience" not in result.output
    assert "--refresh-data" in result.output


def test_measurement_cli_exposes_readiness_and_lifecycle_commands() -> None:
    result = CliRunner().invoke(aivc_app, ["measurement", "--help"])
    assert result.exit_code == 0, result.output
    assert "check" in result.output
    assert "start" in result.output
    assert "run" in result.output
    assert "status" in result.output
