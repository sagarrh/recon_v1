from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.utils.text import normalize_name


def partition_valid_runs(runs: list[NormalizedRun]) -> dict[str, list[NormalizedRun]]:
    histories: dict[str, list[NormalizedRun]] = defaultdict(list)
    for run in runs:
        if run.is_valid:
            histories[run.monitor_query_key].append(run)
    for history in histories.values():
        history.sort(key=lambda item: (item.created_at, str(item.run_id)))
    return dict(histories)


def trend_status(values: list[float]) -> str:
    if not values:
        return "no_data"
    if len(values) == 1:
        return "insufficient_history"
    delta = values[-1] - values[0]
    volatility = float(np.std(values))
    if len(values) >= 3:
        prior = values[:-1]
        prior_min, prior_max = min(prior), max(prior)
        if values[-1] > prior_max + 0.1:
            return "one-time spike"
        if values[-1] < prior_min - 0.1:
            return "one-time drop"
    if volatility >= 0.2 and abs(delta) < 0.1:
        return "volatile"
    if delta >= 0.1:
        return "increasing"
    if delta <= -0.1:
        return "decreasing"
    return "stable"


def rolling_baseline(values: list[float], window: int = 5) -> dict[str, float | int | None]:
    prior = values[-(window + 1) : -1]
    if not prior:
        return {
            "observation_count": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "minimum": None,
            "maximum": None,
            "change_from_mean": None,
            "z_score": None,
        }
    array = np.asarray(prior, dtype=float)
    mean = float(np.mean(array))
    standard_deviation = float(np.std(array))
    latest = values[-1]
    return {
        "observation_count": len(prior),
        "mean": mean,
        "median": float(np.median(array)),
        "standard_deviation": standard_deviation,
        "minimum": float(np.min(array)),
        "maximum": float(np.max(array)),
        "change_from_mean": latest - mean,
        "z_score": (
            (latest - mean) / standard_deviation
            if len(prior) >= 3 and standard_deviation > 1e-12
            else None
        ),
    }


def aggregate_visibility_timeline(
    histories: dict[str, list[NormalizedRun]],
    company_name: str,
    *,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    target = normalize_name(company_name)
    events = sorted(
        (
            (run.created_at, query_key, run)
            for query_key, history in histories.items()
            for run in history
            if provider is None or run.service == provider
        ),
        key=lambda item: (item[0], item[1], str(item[2].run_id)),
    )
    state: dict[str, NormalizedRun] = {}
    timeline: list[dict[str, Any]] = []
    for created_at, query_key, run in events:
        if target not in run.company_metrics:
            continue
        state[query_key] = run
        literal_count = sum(
            current.company_metrics[target].literal_answer_count for current in state.values()
        )
        answer_count = sum(len(current.answers) for current in state.values())
        timeline.append(
            {
                "at": created_at.isoformat(),
                "run_id": str(run.run_id),
                "updated_query_key": query_key,
                "literal_answer_count": literal_count,
                "answer_count": answer_count,
                "visibility": literal_count / answer_count if answer_count else 0.0,
            }
        )
    return timeline


def provider_intelligence(
    histories: dict[str, list[NormalizedRun]], company_name: str
) -> list[dict[str, Any]]:
    providers = sorted({run.service for history in histories.values() for run in history})
    result: list[dict[str, Any]] = []
    for provider in providers:
        points = aggregate_visibility_timeline(histories, company_name, provider=provider)
        if not points:
            continue
        values = [float(point["visibility"]) for point in points]
        provider_runs = [
            run for history in histories.values() for run in history if run.service == provider
        ]
        result.append(
            {
                "provider": provider,
                "run_count": len(provider_runs),
                "query_count": len({run.monitor_query_key for run in provider_runs}),
                "latest_visibility": values[-1],
                "average_visibility": float(np.mean(values)),
                "trend": trend_status(values),
                "volatility": float(np.std(values)),
                "first_observed_at": points[0]["at"],
                "latest_observed_at": points[-1]["at"],
                "latest_literal_answer_count": points[-1]["literal_answer_count"],
                "latest_total_answer_count": points[-1]["answer_count"],
                "history": points,
            }
        )
    return result


def query_intelligence(
    histories: dict[str, list[NormalizedRun]], company_name: str
) -> list[dict[str, Any]]:
    target = normalize_name(company_name)
    result: list[dict[str, Any]] = []
    for query_key, history in histories.items():
        values = [
            run.company_metrics[target].literal_visibility
            for run in history
            if target in run.company_metrics
        ]
        if not values:
            continue
        latest = history[-1]
        points = [
            {
                "run_id": str(run.run_id),
                "created_at": run.created_at.isoformat(),
                "literal_visibility": run.company_metrics[target].literal_visibility,
                "literal_answer_count": run.company_metrics[target].literal_answer_count,
                "answer_count": len(run.answers),
            }
            for run in history
            if target in run.company_metrics
        ]
        result.append(
            {
                "monitor_query_key": query_key,
                "query": latest.base_query,
                "provider": latest.service,
                "method": latest.method,
                "cluster_id": latest.cluster_id,
                "cluster_name": latest.cluster_name,
                "run_count": len(points),
                "current_visibility": values[-1],
                "latest_delta": values[-1] - values[-2] if len(values) >= 2 else None,
                "trend": trend_status(values),
                "rolling_baseline": rolling_baseline(values),
                "history": points,
            }
        )
    return sorted(result, key=lambda item: (item["provider"], item["query"]))
