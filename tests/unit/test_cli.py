from __future__ import annotations

from typing import Any

from conftest import CLIENT_ID
from typer.testing import CliRunner

from ai_visibility.cli.app import app
from ai_visibility.config.settings import Settings
from ai_visibility.normalization.models import RawMonitoringRun


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
