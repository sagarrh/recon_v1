from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from aivc.measurement.models import SourceName, SourceSnapshot, WindowType
from aivc.measurement.targets import (
    normalize_query,
    normalized_page_targets,
    normalized_queries,
    page_matches,
)


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _sum(rows: Iterable[Mapping[str, Any]], key: str) -> Decimal | None:
    values = [number for row in rows if (number := _number(row.get(key))) is not None]
    return sum(values, Decimal(0)) if values else None


def _json_number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _checksum(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def aggregate_gsc(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_pages: list[str],
    target_queries: list[str],
) -> tuple[dict[str, Any], list[str]]:
    pages = normalized_page_targets(target_pages)
    queries = normalized_queries(target_queries)
    warnings: list[str] = []
    if not pages and not queries:
        return {}, ["gsc_targets_missing"]
    matched: list[Mapping[str, Any]] = []
    for row in rows:
        page_ok = not pages or page_matches(str(row.get("page") or ""), pages)
        query_ok = not queries or normalize_query(str(row.get("query") or "")) in queries
        if page_ok and query_ok:
            matched.append(row)
    natural_keys = [
        (
            row.get("metric_date"),
            normalize_query(str(row.get("query") or "")),
            str(row.get("page") or ""),
            row.get("country"),
            row.get("device"),
        )
        for row in matched
    ]
    if len(natural_keys) != len(set(natural_keys)):
        warnings.append("gsc_duplicate_natural_keys")
    clicks = _sum(matched, "clicks") or Decimal(0)
    impressions = _sum(matched, "impressions") or Decimal(0)
    weighted_position = Decimal(0)
    position_weight = Decimal(0)
    for row in matched:
        position = _number(row.get("position"))
        weight = _number(row.get("impressions"))
        if position is not None and weight is not None and weight > 0:
            weighted_position += position * weight
            position_weight += weight
    if pages:
        mapping_confidence = "exact"
    else:
        mapping_confidence = "query_only"
        warnings.append("gsc_query_only_mapping")
    metrics = {
        "mapping_confidence": mapping_confidence,
        "matched_row_count": len(matched),
        "clicks": int(clicks),
        "impressions": int(impressions),
        "ctr_ratio": _json_number(clicks / impressions) if impressions else None,
        "average_position": (
            _json_number(weighted_position / position_weight) if position_weight else None
        ),
        "distinct_queries": len({normalize_query(str(row.get("query") or "")) for row in matched}),
        "distinct_pages": len({str(row.get("page") or "") for row in matched}),
    }
    return metrics, warnings


def aggregate_ga4(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_pages: list[str],
) -> tuple[dict[str, Any], list[str]]:
    pages = normalized_page_targets(target_pages)
    if not pages:
        return {}, ["ga4_page_targets_missing", "property_wide_ga4_refused"]
    matched = [row for row in rows if page_matches(str(row.get("landing_page") or ""), pages)]
    natural_keys = [
        (
            row.get("metric_date"),
            row.get("source"),
            row.get("medium"),
            row.get("campaign"),
            str(row.get("landing_page") or ""),
            row.get("country"),
            row.get("device"),
        )
        for row in matched
    ]
    warnings: list[str] = []
    if len(natural_keys) != len(set(natural_keys)):
        warnings.append("ga4_duplicate_natural_keys")
    metrics = {
        "mapping_confidence": "exact",
        "matched_row_count": len(matched),
        "sessions": _json_number(_sum(matched, "sessions")),
        "engaged_sessions": _json_number(_sum(matched, "engaged_sessions")),
        "conversions": _json_number(_sum(matched, "conversions")),
        "recorded_revenue": _json_number(_sum(matched, "revenue")),
        "rows_with_recorded_revenue": sum(1 for row in matched if row.get("revenue") is not None),
        "distinct_landing_pages": len({str(row.get("landing_page") or "") for row in matched}),
        "excluded_non_additive_metrics": ["total_users", "new_users"],
    }
    return metrics, warnings


def make_snapshot(
    *,
    source: SourceName,
    window_type: WindowType,
    requested_start: date,
    requested_end: date,
    rows: Sequence[Mapping[str, Any]],
    target_pages: list[str],
    target_queries: list[str],
    connected: bool,
    fresh_through: date | None,
) -> SourceSnapshot:
    if not connected:
        status = "not_connected"
        metrics: dict[str, Any] = {}
        warnings = [f"{source.value}_not_connected"]
    elif source is SourceName.ga4 and not target_pages:
        status = "unmapped"
        metrics, warnings = aggregate_ga4(rows, target_pages=target_pages)
    else:
        if source is SourceName.gsc:
            metrics, warnings = aggregate_gsc(
                rows,
                target_pages=target_pages,
                target_queries=target_queries,
            )
        else:
            metrics, warnings = aggregate_ga4(rows, target_pages=target_pages)
        duplicate_warning = f"{source.value}_duplicate_natural_keys"
        if duplicate_warning in warnings:
            status = "refused"
            warnings.append("duplicate_rows_not_aggregated")
        elif not metrics:
            status = "unmapped"
        elif fresh_through is None or fresh_through < requested_end:
            status = "stale"
            warnings.append(f"{source.value}_window_not_fresh")
        else:
            status = "available"
            if int(metrics.get("matched_row_count") or 0) == 0:
                warnings.append(f"{source.value}_no_matching_rows")
    metric_dates: list[date] = []
    for row in rows:
        metric_date = row.get("metric_date")
        if isinstance(metric_date, date):
            metric_dates.append(metric_date)
    expected_days = (requested_end - requested_start).days + 1
    observed_days = len(set(metric_dates))
    coverage_ratio = observed_days / expected_days if expected_days > 0 else 0.0
    metrics["source_expected_days"] = expected_days
    metrics["source_observed_days"] = observed_days
    metrics["source_date_coverage_ratio"] = coverage_ratio
    if status == "available" and coverage_ratio < 0.8:
        warnings.append(f"{source.value}_window_date_coverage_low")
    payload = {
        "source": source.value,
        "window_type": window_type.value,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "fresh_through": fresh_through,
        "row_count": int(metrics.get("matched_row_count") or 0),
        "source_rows_scanned": len(rows),
        "metrics": metrics,
        "source_status": status,
        "warnings": sorted(set(warnings)),
    }
    return SourceSnapshot(
        source=source,
        window_type=window_type,
        requested_start=requested_start,
        requested_end=requested_end,
        effective_start=min(metric_dates) if metric_dates else None,
        effective_end=max(metric_dates) if metric_dates else None,
        fresh_through=fresh_through,
        row_count=int(metrics.get("matched_row_count") or 0),
        metrics=metrics,
        source_status=status,
        warnings=sorted(set(warnings)),
        payload_checksum=_checksum(payload),
    )
