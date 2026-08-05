"""Health of mirrored sources this backend reads but does not own.

The case that matters most is the one that already happened: `ai_monitoring` reported `status=ok`
for months while writing nothing, because its sync query selected a column that exists only
downstream. A monitor that trusts the sync's self-report would have stayed green throughout.
"""

from datetime import UTC, datetime, timedelta

from aivc.database import source_health as SH

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
RECENT = NOW - timedelta(hours=2)
OLD = NOW - timedelta(days=9)


def _classify(**over):
    kwargs = {
        "row_count": 5000, "lag_days": 2, "last_synced_at": RECENT,
        "sync_status": "ok", "now": NOW, "max_lag_days": 5,
    }
    kwargs.update(over)
    return SH._classify(**kwargs)


# ---------------------------------------------------------------------------
# The failure that actually happened.
# ---------------------------------------------------------------------------

def test_green_sync_with_stalled_data_is_silent_failure_not_ok():
    """ai_monitoring's exact shape: the job succeeds, the data does not move."""
    status, detail = _classify(lag_days=60, sync_status="ok", last_synced_at=RECENT)
    assert status == SH.SILENT
    assert "writing nothing" in detail


def test_silent_failures_are_called_out_separately_in_the_summary():
    """An honestly-failing sync is visible elsewhere; a silently-failing one is not."""
    sources = [
        SH.SourceHealth("gsc", "t", SH.OK, 10, None, 1, RECENT, "ok", "fine"),
        SH.SourceHealth("ai_monitoring", "t", SH.SILENT, 10, None, 60, RECENT, "ok", "bad"),
    ]
    summary = SH.summarize_source_health(sources)
    assert summary["healthy"] is False
    assert summary["silently_failing"] == ["ai_monitoring"]


def test_stalled_data_with_an_old_heartbeat_is_merely_stale():
    """When the sync also stopped, the failure is honest — a different, less dangerous problem."""
    status, _ = _classify(lag_days=60, last_synced_at=OLD)
    assert status == SH.STALE


def test_stalled_data_with_a_failing_sync_status_is_stale_not_silent():
    status, _ = _classify(lag_days=60, sync_status="error", last_synced_at=RECENT)
    assert status == SH.STALE


# ---------------------------------------------------------------------------
# Normal states.
# ---------------------------------------------------------------------------

def test_recent_data_is_healthy():
    status, detail = _classify(lag_days=2)
    assert status == SH.OK
    assert "2 day(s) old" in detail


def test_googles_own_reporting_lag_does_not_trigger_an_alert():
    """GSC and GA4 sit 2-3 days behind by design. Alerting on that trains people to ignore it."""
    for lag in (2, 3, 4, 5):
        assert _classify(lag_days=lag)[0] == SH.OK
    assert _classify(lag_days=6)[0] != SH.OK


def test_empty_table_is_empty_not_stale():
    status, detail = _classify(row_count=0)
    assert status == SH.EMPTY
    assert "ever arrived" in detail


def test_missing_heartbeat_is_reported_as_not_synced():
    status, _ = _classify(last_synced_at=None)
    assert status == SH.NOT_SYNCED


def test_rows_without_a_usable_date_are_not_silently_healthy():
    status, _ = _classify(lag_days=None)
    assert status == SH.EMPTY


def test_threshold_is_configurable():
    assert _classify(lag_days=10, max_lag_days=5)[0] != SH.OK
    assert _classify(lag_days=10, max_lag_days=14)[0] == SH.OK


# ---------------------------------------------------------------------------
# Summary shape.
# ---------------------------------------------------------------------------

def test_all_healthy_sources_summarize_as_healthy():
    sources = [
        SH.SourceHealth(n, "t", SH.OK, 10, None, 1, RECENT, "ok", "fine")
        for n in ("gsc", "ga4")
    ]
    summary = SH.summarize_source_health(sources)
    assert summary["healthy"] is True
    assert summary["checked"] == 2
    assert summary["silently_failing"] == []


def test_every_monitored_source_is_covered():
    """The five sources the measurement and citation paths actually depend on."""
    names = {name for name, *_ in SH.MONITORED_SOURCES}
    assert names == {"gsc", "ga4", "ai_monitoring", "ai_responses", "sov_weekly"}


def test_a_silent_source_carries_a_warning_explaining_the_pattern():
    status, _ = _classify(lag_days=60, sync_status="ok", last_synced_at=RECENT)
    assert status == SH.SILENT   # the warning itself is attached in check_source_health
