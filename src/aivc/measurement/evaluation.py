from __future__ import annotations

from typing import Any
from uuid import UUID

from aivc.measurement.models import MeasurementOutcome, SourceName, SourceSnapshot


def _number(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _standard_delta(
    name: str,
    baseline: float | None,
    follow_up: float | None,
    *,
    unit: str,
    invert: bool = False,
) -> dict[str, Any]:
    delta = None if baseline is None or follow_up is None else follow_up - baseline
    improvement = -delta if invert and delta is not None else delta
    relative = None
    if delta is not None and baseline not in (None, 0):
        relative = (delta / abs(baseline)) * 100
    return {
        "metric": name,
        "unit": unit,
        "baseline": baseline,
        "follow_up": follow_up,
        "delta": delta,
        "relative_change_percent": relative,
        "improvement_direction": improvement,
    }


def _gsc_deltas(baseline: SourceSnapshot, follow_up: SourceSnapshot) -> list[dict[str, Any]]:
    deltas = [
        _standard_delta(
            "clicks",
            _number(baseline.metrics, "clicks"),
            _number(follow_up.metrics, "clicks"),
            unit="count",
        ),
        _standard_delta(
            "impressions",
            _number(baseline.metrics, "impressions"),
            _number(follow_up.metrics, "impressions"),
            unit="count",
        ),
        _standard_delta(
            "ctr",
            _scaled(_number(baseline.metrics, "ctr_ratio"), 100),
            _scaled(_number(follow_up.metrics, "ctr_ratio"), 100),
            unit="percentage_points",
        ),
        _standard_delta(
            "average_position",
            _number(baseline.metrics, "average_position"),
            _number(follow_up.metrics, "average_position"),
            unit="position",
            invert=True,
        ),
    ]
    return [
        delta for delta in deltas if delta["baseline"] is not None or delta["follow_up"] is not None
    ]


def _scaled(value: float | None, multiplier: float) -> float | None:
    return value * multiplier if value is not None else None


def _ga4_deltas(baseline: SourceSnapshot, follow_up: SourceSnapshot) -> list[dict[str, Any]]:
    return [
        _standard_delta(
            name,
            _number(baseline.metrics, name),
            _number(follow_up.metrics, name),
            unit="currency" if name == "recorded_revenue" else "count",
        )
        for name in ("sessions", "engaged_sessions", "conversions", "recorded_revenue")
        if _number(baseline.metrics, name) is not None
        or _number(follow_up.metrics, name) is not None
    ]


def _direction_values(
    gsc_deltas: list[dict[str, Any]], ga4_deltas: list[dict[str, Any]]
) -> list[float]:
    preferred: list[float] = []
    for delta in gsc_deltas:
        if delta["metric"] == "clicks" and delta["improvement_direction"] is not None:
            preferred.append(float(delta["improvement_direction"]))
    for delta in ga4_deltas:
        if delta["metric"] in ("conversions", "recorded_revenue"):
            value = delta["improvement_direction"]
            if value is not None:
                preferred.append(float(value))
    if preferred:
        return preferred
    fallback: list[float] = []
    for delta in [*gsc_deltas, *ga4_deltas]:
        value = delta.get("improvement_direction")
        if value is not None:
            fallback.append(float(value))
    return fallback


def evaluate_snapshots(
    *,
    plan_id: UUID,
    snapshots: list[SourceSnapshot],
    currency_code: str | None,
    overlapping_actions: bool = False,
) -> MeasurementOutcome:
    indexed = {(snapshot.source, snapshot.window_type.value): snapshot for snapshot in snapshots}
    available_pairs: dict[SourceName, tuple[SourceSnapshot, SourceSnapshot]] = {}
    for source in SourceName:
        baseline = indexed.get((source, "baseline"))
        follow_up = indexed.get((source, "follow_up"))
        if (
            baseline is not None
            and follow_up is not None
            and baseline.source_status == "available"
            and follow_up.source_status == "available"
        ):
            available_pairs[source] = (baseline, follow_up)

    gsc_deltas = (
        _gsc_deltas(*available_pairs[SourceName.gsc]) if SourceName.gsc in available_pairs else []
    )
    ga4_deltas = (
        _ga4_deltas(*available_pairs[SourceName.ga4]) if SourceName.ga4 in available_pairs else []
    )
    directions = _direction_values(gsc_deltas, ga4_deltas)
    positive = any(value > 0 for value in directions)
    negative = any(value < 0 for value in directions)
    if not directions:
        classification = "insufficient_evidence"
    elif positive and negative:
        classification = "mixed"
    elif positive:
        classification = "observed_increase"
    elif negative:
        classification = "observed_decrease"
    else:
        classification = "no_observed_change"

    warnings = sorted({warning for snapshot in snapshots for warning in snapshot.warnings})
    limitations = ["observational_before_after_comparison_not_causal"]
    if overlapping_actions:
        limitations.append("overlapping_actions_prevent_clean_attribution")
    if SourceName.gsc not in available_pairs:
        limitations.append("complete_gsc_pair_unavailable")
    if SourceName.ga4 not in available_pairs:
        limitations.append("complete_ga4_pair_unavailable")

    mapping_confidences = {
        str(snapshot.metrics.get("mapping_confidence"))
        for pair in available_pairs.values()
        for snapshot in pair
    }
    if "query_only" in mapping_confidences:
        limitations.append("query_only_mapping")

    follow_ga4 = available_pairs.get(SourceName.ga4, (None, None))[1]
    recorded_revenue = (
        _number(follow_ga4.metrics, "recorded_revenue") if follow_ga4 is not None else None
    )
    baseline_revenue = None
    if SourceName.ga4 in available_pairs:
        baseline_revenue = _number(available_pairs[SourceName.ga4][0].metrics, "recorded_revenue")
    observed_revenue_delta = (
        recorded_revenue - baseline_revenue
        if recorded_revenue is not None and baseline_revenue is not None
        else None
    )
    revenue_category = (
        "recorded" if recorded_revenue is not None and currency_code else "unavailable"
    )
    if recorded_revenue is not None:
        limitations.append("recorded_revenue_is_not_reconv1_attributed_revenue")
    if recorded_revenue is not None and not currency_code:
        limitations.append("revenue_currency_missing")

    available_count = len(available_pairs)
    if not directions:
        confidence = "none"
    elif overlapping_actions or "query_only" in mapping_confidences:
        confidence = "low"
    elif available_count == 2 and not warnings:
        confidence = "high"
    else:
        confidence = "medium"
    return MeasurementOutcome(
        plan_id=plan_id,
        classification=classification,
        confidence=confidence,
        gsc_deltas=gsc_deltas,
        ga4_deltas=ga4_deltas,
        revenue_category=revenue_category,
        recorded_revenue=recorded_revenue if revenue_category == "recorded" else None,
        observed_revenue_delta=(observed_revenue_delta if revenue_category == "recorded" else None),
        currency_code=currency_code if revenue_category == "recorded" else None,
        evidence_summary={
            "available_sources": sorted(source.value for source in available_pairs),
            "causal_claim": False,
            "overlapping_actions": overlapping_actions,
        },
        limitations=sorted(set(limitations)),
        warnings=warnings,
    )
