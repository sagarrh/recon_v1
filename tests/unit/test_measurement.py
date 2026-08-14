from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from aivc.measurement.aggregation import aggregate_ga4, aggregate_gsc, make_snapshot
from aivc.measurement.evaluation import evaluate_snapshots
from aivc.measurement.models import (
    SourceName,
    StartMeasurementRequest,
    SubjectType,
    VerificationStatus,
    WindowType,
)
from aivc.measurement.planning import measurement_windows, plan_idempotency_key
from aivc.measurement.targets import normalize_page_target, normalize_query, page_matches

CLIENT_ID = UUID("b88e87f3-0aa5-4da9-be48-2807b12d5a91")
PLAN_ID = UUID("d9ccf75a-ff29-4e7f-af2d-90cd5dc6f13a")


def _request(**updates: object) -> StartMeasurementRequest:
    values: dict[str, object] = {
        "client_id": CLIENT_ID,
        "subject_type": SubjectType.recon_recommendation,
        "subject_id": "24d91e88-acde-4cd5-9f85-326938e5739d",
        "implemented_at": datetime(2026, 8, 1, 12, tzinfo=UTC),
        "implemented_by": "customer-success@example.com",
        "verification_status": VerificationStatus.manual_confirmed,
        "target_pages": ["https://example.com/guide/?utm_source=test"],
        "target_queries": ["  Procurement   automation "],
    }
    values.update(updates)
    return StartMeasurementRequest.model_validate(values)


def test_measurement_windows_are_fixed_around_implementation() -> None:
    windows = measurement_windows(_request())
    assert windows.baseline_start == date(2026, 7, 4)
    assert windows.baseline_end == date(2026, 7, 31)
    assert windows.follow_up_start == date(2026, 8, 8)
    assert windows.follow_up_end == date(2026, 9, 4)


def test_plan_idempotency_ignores_target_order_and_whitespace() -> None:
    first = _request(target_queries=["Procurement automation", "Supplier risk"])
    second = _request(target_queries=[" supplier  RISK ", "procurement AUTOMATION"])
    assert plan_idempotency_key(first) == plan_idempotency_key(second)


def test_measurement_request_requires_timezone_and_targets() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _request(implemented_at=datetime(2026, 8, 1, 12))
    with pytest.raises(ValueError, match="target"):
        _request(target_pages=[], target_queries=[])
    with pytest.raises(ValueError, match="GA4"):
        _request(require_ga4=True, target_pages=[])


def test_page_and_query_normalization_are_exact() -> None:
    target = normalize_page_target("https://Example.com/a/page/?utm_source=x")
    assert target is not None
    assert page_matches("/a/page", [target])
    assert not page_matches("https://other.example/a/page", [target])
    assert normalize_query("  Best   PROCUREMENT Tool ") == "best procurement tool"


def test_gsc_aggregation_recomputes_ctr_and_weights_position() -> None:
    rows = [
        {
            "metric_date": date(2026, 7, 1),
            "query": "Procurement automation",
            "page": "https://example.com/guide",
            "clicks": 10,
            "impressions": 100,
            "ctr": 0.99,
            "position": 2,
            "country": "usa",
            "device": "desktop",
        },
        {
            "metric_date": date(2026, 7, 2),
            "query": "procurement automation",
            "page": "https://example.com/guide/",
            "clicks": 5,
            "impressions": 50,
            "ctr": 0.01,
            "position": 8,
            "country": "usa",
            "device": "desktop",
        },
        {
            "metric_date": date(2026, 7, 2),
            "query": "Unrelated",
            "page": "https://example.com/other",
            "clicks": 500,
            "impressions": 500,
            "position": 1,
            "country": "usa",
            "device": "desktop",
        },
    ]
    metrics, warnings = aggregate_gsc(
        rows,
        target_pages=["https://example.com/guide"],
        target_queries=["Procurement automation"],
    )
    assert warnings == []
    assert metrics["matched_row_count"] == 2
    assert metrics["clicks"] == 15
    assert metrics["impressions"] == 150
    assert metrics["ctr_ratio"] == pytest.approx(0.1)
    assert metrics["average_position"] == pytest.approx(4.0)


def test_ga4_refuses_property_wide_attribution_and_sums_only_target_page() -> None:
    rows = [
        {
            "metric_date": date(2026, 7, 1),
            "source": "google",
            "medium": "organic",
            "campaign": None,
            "landing_page": "/guide/",
            "country": "US",
            "device": "desktop",
            "sessions": 20,
            "engaged_sessions": 10,
            "conversions": 2,
            "revenue": 100,
        },
        {
            "metric_date": date(2026, 7, 1),
            "source": "google",
            "medium": "organic",
            "campaign": None,
            "landing_page": "/unrelated",
            "country": "US",
            "device": "desktop",
            "sessions": 999,
            "engaged_sessions": 999,
            "conversions": 99,
            "revenue": 9999,
        },
    ]
    refused, warnings = aggregate_ga4(rows, target_pages=[])
    assert refused == {}
    assert "property_wide_ga4_refused" in warnings

    metrics, warnings = aggregate_ga4(rows, target_pages=["https://example.com/guide"])
    assert warnings == []
    assert metrics["sessions"] == 20
    assert metrics["conversions"] == 2
    assert metrics["recorded_revenue"] == 100


def test_duplicate_metric_keys_are_refused_instead_of_double_counted() -> None:
    row = {
        "metric_date": date(2026, 7, 1),
        "query": "procurement automation",
        "page": "/guide",
        "clicks": 10,
        "impressions": 100,
        "position": 3,
        "country": "US",
        "device": "desktop",
    }
    snapshot = make_snapshot(
        source=SourceName.gsc,
        window_type=WindowType.baseline,
        requested_start=date(2026, 7, 1),
        requested_end=date(2026, 7, 1),
        rows=[row, row],
        target_pages=["/guide"],
        target_queries=["procurement automation"],
        connected=True,
        fresh_through=date(2026, 7, 1),
    )
    assert snapshot.source_status == "refused"
    assert "duplicate_rows_not_aggregated" in snapshot.warnings


def test_outcome_reports_recorded_revenue_without_claiming_causation() -> None:
    def snapshot(source: SourceName, window: WindowType, metrics: dict[str, object]) -> object:
        return make_snapshot(
            source=source,
            window_type=window,
            requested_start=date(2026, 7, 1),
            requested_end=date(2026, 7, 1),
            rows=[
                {
                    "metric_date": date(2026, 7, 1),
                    "query": "procurement automation",
                    "page": "/guide",
                    "landing_page": "/guide",
                    "clicks": metrics.get("clicks"),
                    "impressions": metrics.get("impressions"),
                    "position": metrics.get("position"),
                    "sessions": metrics.get("sessions"),
                    "engaged_sessions": metrics.get("sessions"),
                    "conversions": metrics.get("conversions"),
                    "revenue": metrics.get("revenue"),
                    "source": "google",
                    "medium": "organic",
                    "campaign": None,
                    "country": "US",
                    "device": "desktop",
                }
            ],
            target_pages=["/guide"],
            target_queries=["procurement automation"],
            connected=True,
            fresh_through=date(2026, 7, 1),
        )

    snapshots = [
        snapshot(
            SourceName.gsc, WindowType.baseline, {"clicks": 10, "impressions": 100, "position": 5}
        ),
        snapshot(
            SourceName.gsc, WindowType.follow_up, {"clicks": 20, "impressions": 150, "position": 3}
        ),
        snapshot(
            SourceName.ga4, WindowType.baseline, {"sessions": 30, "conversions": 1, "revenue": 100}
        ),
        snapshot(
            SourceName.ga4, WindowType.follow_up, {"sessions": 50, "conversions": 2, "revenue": 150}
        ),
    ]
    outcome = evaluate_snapshots(
        plan_id=PLAN_ID,
        snapshots=snapshots,  # type: ignore[arg-type]
        currency_code="USD",
    )
    assert outcome.classification == "observed_increase"
    assert outcome.confidence == "high"
    assert outcome.revenue_category == "recorded"
    assert outcome.recorded_revenue == 150
    assert outcome.observed_revenue_delta == 50
    assert outcome.evidence_summary["causal_claim"] is False
    assert "recorded_revenue_is_not_reconv1_attributed_revenue" in outcome.limitations
