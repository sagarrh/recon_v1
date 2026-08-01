from __future__ import annotations

from collections import Counter
from typing import Any

from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.utils.text import literal_mentions


def _answer_company_sets(run: NormalizedRun) -> list[set[str]]:
    companies = [metric for metric in run.company_metrics.values()]
    result: list[set[str]] = []
    for answer in run.answers:
        present = {
            metric.company_name
            for metric in companies
            if literal_mentions(answer.text, metric.aliases)[0] > 0
        }
        result.append(present)
    return result


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def detect_recommendation_pattern(
    previous: NormalizedRun, current: NormalizedRun
) -> dict[str, Any] | None:
    current_sets = [item for item in _answer_company_sets(current) if len(item) >= 3]
    if not current_sets or not current.answers:
        return None
    best_cluster: list[set[str]] = []
    for seed in current_sets:
        cluster = [item for item in current_sets if _jaccard(seed, item) >= 0.65]
        if len(cluster) > len(best_cluster):
            best_cluster = cluster
    frequency = len(best_cluster) / len(current.answers)
    if frequency < 0.4:
        return None
    counts = Counter(name for item in best_cluster for name in item)
    representative = {name for name, count in counts.items() if count / len(best_cluster) >= 0.75}
    if len(representative) < 3:
        largest_bundle = best_cluster[0]
        for bundle in best_cluster[1:]:
            if len(bundle) > len(largest_bundle):
                largest_bundle = bundle
        representative = set(largest_bundle)
    previous_sets = _answer_company_sets(previous)
    previous_matches = sum(
        1 for item in previous_sets if item and _jaccard(representative, item) >= 0.65
    )
    previous_frequency = previous_matches / len(previous.answers) if previous.answers else 0.0
    delta = frequency - previous_frequency
    if delta < 0.25:
        return None
    return {
        "signal_type": "recommendation_pattern_shift",
        "bundle": sorted(representative),
        "previous_frequency": previous_frequency,
        "current_frequency": frequency,
        "frequency_delta": delta,
        "current_matching_answer_count": len(best_cluster),
        "confidence": "medium",
    }
