from __future__ import annotations

from itertools import pairwise
from typing import Any

from ai_visibility.analysis.citations import citation_deltas
from ai_visibility.analysis.competitors import competitor_deltas
from ai_visibility.analysis.recommendation_patterns import detect_recommendation_pattern
from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.utils.text import normalize_name


def adjacent_comparisons(
    histories: dict[str, list[NormalizedRun]], company_name: str
) -> list[dict[str, Any]]:
    target = normalize_name(company_name)
    result: list[dict[str, Any]] = []
    for query_key, history in histories.items():
        for previous, current in pairwise(history):
            before = previous.company_metrics.get(target)
            after = current.company_metrics.get(target)
            if before is None or after is None:
                continue
            comparability_flags = sorted(
                set(previous.data_quality_flags + current.data_quality_flags)
                & {"execution_configuration_incomplete"}
            )
            score = 0.75 if comparability_flags else 1.0
            result.append(
                {
                    "monitor_query_key": query_key,
                    "query": current.base_query,
                    "provider": current.service,
                    "method": current.method,
                    "previous_run_id": str(previous.run_id),
                    "current_run_id": str(current.run_id),
                    "previous_at": previous.created_at.isoformat(),
                    "current_at": current.created_at.isoformat(),
                    "previous_answer_count": len(previous.answers),
                    "current_answer_count": len(current.answers),
                    "previous_literal_answer_count": before.literal_answer_count,
                    "current_literal_answer_count": after.literal_answer_count,
                    "previous_literal_visibility": before.literal_visibility,
                    "current_literal_visibility": after.literal_visibility,
                    "literal_visibility_delta": (
                        after.literal_visibility - before.literal_visibility
                    ),
                    "upstream_visibility_delta": (
                        after.upstream_visibility - before.upstream_visibility
                        if after.upstream_visibility is not None
                        and before.upstream_visibility is not None
                        else None
                    ),
                    "metric_difference": after.metric_difference,
                    "comparability_score": score,
                    "comparability_flags": comparability_flags,
                    "citation_deltas": citation_deltas(previous, current, company_name),
                    "competitor_deltas": competitor_deltas(previous, current, company_name),
                    "recommendation_pattern": detect_recommendation_pattern(previous, current),
                }
            )
    return sorted(result, key=lambda item: (item["current_at"], item["provider"]))


def material_comparisons(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in comparisons
        if abs(float(item["literal_visibility_delta"])) >= 0.1
        or abs(
            int(item["current_literal_answer_count"]) - int(item["previous_literal_answer_count"])
        )
        >= 2
    ]
