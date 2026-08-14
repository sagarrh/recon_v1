from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from aivc.measurement.models import (
    MeasurementWindows,
    SourceName,
    StartMeasurementRequest,
)
from aivc.measurement.targets import normalize_query, normalized_page_targets


def measurement_windows(request: StartMeasurementRequest) -> MeasurementWindows:
    anchor = request.implemented_at.date()
    baseline_end = anchor - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=request.baseline_days - 1)
    follow_up_start = anchor + timedelta(days=max(1, request.stabilization_days))
    follow_up_end = follow_up_start + timedelta(days=request.follow_up_days - 1)
    return MeasurementWindows(
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        follow_up_start=follow_up_start,
        follow_up_end=follow_up_end,
    )


def normalized_targets(request: StartMeasurementRequest) -> tuple[list[str], list[str]]:
    pages = [
        f"https://{target.host}{target.path}" if target.host else target.path
        for target in normalized_page_targets(request.target_pages)
    ]
    queries = sorted(
        {value for query in request.target_queries if (value := normalize_query(query))}
    )
    return sorted(pages), queries


def required_sources(request: StartMeasurementRequest) -> list[SourceName]:
    return [SourceName.gsc, SourceName.ga4] if request.require_ga4 else [SourceName.gsc]


def plan_idempotency_key(request: StartMeasurementRequest) -> str:
    pages, queries = normalized_targets(request)
    windows = measurement_windows(request)
    payload = {
        "client_id": str(request.client_id),
        "subject_type": request.subject_type.value,
        "subject_id": request.subject_id.strip(),
        "implemented_at": request.implemented_at.isoformat(),
        "pages": pages,
        "queries": queries,
        "windows": windows.model_dump(mode="json"),
        "required_sources": [source.value for source in required_sources(request)],
        "policy": "measurement_v1",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
