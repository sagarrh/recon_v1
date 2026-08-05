# test_execution_anchor.py — outcome measurement is anchored on a confirmed implemented_at.
# This is the guarantee the whole execution-tracking phase exists for: Recon must never report that
# growth followed an ACTION when all it knows is that growth followed a REPORT.
import types
from datetime import date

import scout.db.outcome_measure as OM
from scout.db import executions as EX

TODAY = date(2026, 11, 1)

# Shipped 2026-06-01, so a 6-week window from the recommendation date elapsed long ago.
# The execution did not happen until 2026-10-20 — only 1.6 weeks before TODAY.
OUTCOME_ROW = {
    "recommendation_id": "r1", "run_id": "run1", "client_id": "c1", "cluster_id": "clu1",
    "week_date": "2026-06-01", "baseline_client_sov_pp": 20.0,
    "baseline_primary_competitor": "Rival", "baseline_revenue_usd": None,
    "target_pages": [],
}


def _patch(monkeypatch, *, executions, rows=None, window_weeks=6):
    monkeypatch.setattr(OM, "load_executions", lambda sb, ids: executions)
    monkeypatch.setattr(OM, "_fetch_timelines",
                        lambda sb, ids: {"r1": {"timeline": "", "window_weeks": window_weeks}})
    monkeypatch.setattr(OM, "_current_client_sov_index", lambda: {("c1", "clu1"): 35.0})
    monkeypatch.setattr(OM, "_decision_field", lambda sb, run_id, cluster_id: None)
    monkeypatch.setattr(OM, "_measure_revenue", lambda sb, r: (None, "unavailable"))

    captured: dict = {}

    class _Table:
        def select(self, *a, **k):
            return self

        def is_(self, *a, **k):
            return self

        def execute(self):
            return types.SimpleNamespace(data=list(rows if rows is not None else [OUTCOME_ROW]))

        def upsert(self, payload, **k):
            captured["payload"] = payload
            return self

    monkeypatch.setattr(OM, "m", types.SimpleNamespace(
        SCOUT_OUTCOMES_TABLE="scout_outcomes",
        SCOUT_RECOMMENDATIONS_TABLE="recommendations",
        SCOUT_DECISION_LOG_TABLE="scout_decision_log",
    ))
    sb = types.SimpleNamespace(table=lambda name: _Table())
    return sb, captured


# ---------------------------------------------------------------------------
# The gate: no execution record means no measurement.
# ---------------------------------------------------------------------------

def test_recommendation_without_an_execution_record_is_not_measured(monkeypatch):
    """Its 6-week window elapsed months ago by report date — and it must STILL not be measured."""
    sb, captured = _patch(monkeypatch, executions={})
    assert OM.run_outcome_measurement(sb, TODAY) == 0
    assert "payload" not in captured


def test_execution_claimed_without_a_timestamp_is_not_measured(monkeypatch):
    """status='executed' with no implemented_at is unanchorable; refuse rather than pick a date."""
    sb, captured = _patch(
        monkeypatch, executions={"r1": {"status": "executed", "implemented_at": None}}
    )
    assert OM.run_outcome_measurement(sb, TODAY) == 0
    assert "payload" not in captured


def test_merely_accepted_recommendation_is_not_measured(monkeypatch):
    sb, _ = _patch(
        monkeypatch,
        executions={"r1": {"status": "accepted", "implemented_at": "2026-06-15T00:00:00+00:00"}},
    )
    assert OM.run_outcome_measurement(sb, TODAY) == 0


# ---------------------------------------------------------------------------
# The anchor: the window is counted from implemented_at, not from week_date.
# ---------------------------------------------------------------------------

def test_window_is_counted_from_implemented_at(monkeypatch):
    """Executed 2026-10-20 + 6 weeks lands after TODAY, so nothing is measured yet — even though
    6 weeks from the recommendation's own week_date elapsed back in July."""
    sb, captured = _patch(
        monkeypatch,
        executions={"r1": {"status": "executed", "implemented_at": "2026-10-20T09:00:00+00:00"}},
    )
    assert OM.run_outcome_measurement(sb, TODAY) == 0
    assert "payload" not in captured


def test_measures_once_the_window_from_implementation_has_elapsed(monkeypatch):
    sb, captured = _patch(
        monkeypatch,
        executions={"r1": {"status": "executed", "implemented_at": "2026-08-01T09:00:00+00:00"}},
    )
    assert OM.run_outcome_measurement(sb, TODAY) == 1
    row = captured["payload"][0]
    assert row["window_elapsed_at"] == "2026-09-12"        # 2026-08-01 + 6 weeks
    assert row["implemented_at"] == "2026-08-01T09:00:00+00:00"
    assert row["executed"] is True                          # no longer None
    assert row["sov_delta_pp"] == 15.0                      # 35.0 current - 20.0 baseline
    assert row["recovered"] is True


def test_verified_status_also_measures(monkeypatch):
    sb, captured = _patch(
        monkeypatch,
        executions={"r1": {"status": "verified", "implemented_at": "2026-08-01T09:00:00+00:00"}},
    )
    assert OM.run_outcome_measurement(sb, TODAY) == 1
    assert captured["payload"][0]["execution_status"] == "verified"


def test_measurement_uses_pages_frozen_at_execution_time(monkeypatch):
    """The recommendation may be edited after the fact; what was measured must be what shipped."""
    seen: dict = {}

    def _capture_revenue(sb, r):
        seen["target_pages"] = r.get("target_pages")
        return None, "unavailable"

    sb, captured = _patch(
        monkeypatch,
        executions={"r1": {
            "status": "executed",
            "implemented_at": "2026-08-01T09:00:00+00:00",
            "target_pages": ["https://client.com/as-shipped"],
        }},
    )
    monkeypatch.setattr(OM, "_measure_revenue", _capture_revenue)
    OM.run_outcome_measurement(sb, TODAY)
    assert seen["target_pages"] == ["https://client.com/as-shipped"]
    assert captured["payload"][0]["target_pages"] == ["https://client.com/as-shipped"]


# ---------------------------------------------------------------------------
# is_measurable — the predicate the gate rests on
# ---------------------------------------------------------------------------

def test_is_measurable_requires_both_status_and_timestamp():
    assert EX.is_measurable({"status": "executed", "implemented_at": "2026-08-01T00:00:00+00:00"})
    assert EX.is_measurable({"status": "verified", "implemented_at": "2026-08-01T00:00:00+00:00"})
    assert not EX.is_measurable({"status": "executed", "implemented_at": None})
    assert not EX.is_measurable({"status": "proposed", "implemented_at": "2026-08-01T00:00:00+00:00"})
    assert not EX.is_measurable({})
    assert not EX.is_measurable(None)
