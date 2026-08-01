from __future__ import annotations

from collections import defaultdict
from typing import Any

from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.utils.text import normalize_name


def run_url_stats(run: NormalizedRun, company_name: str) -> dict[str, dict[str, Any]]:
    target = normalize_name(company_name)
    metric = run.company_metrics.get(target)
    company_answers = set(metric.literal_answer_numbers if metric else [])
    stats: dict[str, dict[str, Any]] = {}
    for answer in run.answers:
        for citation in answer.citations:
            item = stats.setdefault(
                citation.normalized_url,
                {
                    "url": citation.normalized_url,
                    "domain": citation.domain,
                    "raw_occurrences": 0,
                    "answer_numbers": set(),
                    "company_cooccurrence_answers": set(),
                    "position_qualities": set(),
                },
            )
            item["raw_occurrences"] += citation.raw_occurrence_count
            item["answer_numbers"].add(answer.answer_number)
            if answer.answer_number in company_answers:
                item["company_cooccurrence_answers"].add(answer.answer_number)
            item["position_qualities"].add(citation.position_quality)
    total_answers = len(run.answers)
    for item in stats.values():
        coverage = len(item.pop("answer_numbers"))
        cooccurrence = len(item.pop("company_cooccurrence_answers"))
        base_rate = len(company_answers) / total_answers if total_answers else 0.0
        item.update(
            {
                "answer_coverage": coverage,
                "company_cooccurrence": cooccurrence,
                "cited_without_company": coverage - cooccurrence,
                "cooccurrence_rate": cooccurrence / coverage if coverage else 0.0,
                "company_base_rate": base_rate,
                "association_lift": (cooccurrence / coverage if coverage else 0.0) - base_rate,
                "position_qualities": sorted(item["position_qualities"]),
            }
        )
    return stats


def citation_deltas(
    previous: NormalizedRun, current: NormalizedRun, company_name: str
) -> list[dict[str, Any]]:
    previous_stats = run_url_stats(previous, company_name)
    current_stats = run_url_stats(current, company_name)
    result: list[dict[str, Any]] = []
    for url in sorted(previous_stats.keys() | current_stats.keys()):
        before = previous_stats.get(url, {})
        after = current_stats.get(url, {})
        previous_coverage = int(before.get("answer_coverage", 0))
        current_coverage = int(after.get("answer_coverage", 0))
        status = (
            "new"
            if not before
            else "removed"
            if not after
            else "increased"
            if current_coverage > previous_coverage
            else "decreased"
            if current_coverage < previous_coverage
            else "persisted"
        )
        result.append(
            {
                "url": url,
                "domain": after.get("domain", before.get("domain")),
                "status": status,
                "previous_raw_occurrences": int(before.get("raw_occurrences", 0)),
                "current_raw_occurrences": int(after.get("raw_occurrences", 0)),
                "previous_answer_coverage": previous_coverage,
                "current_answer_coverage": current_coverage,
                "coverage_delta": current_coverage - previous_coverage,
                "previous_company_cooccurrence": int(before.get("company_cooccurrence", 0)),
                "current_company_cooccurrence": int(after.get("company_cooccurrence", 0)),
                "current_association_lift": float(after.get("association_lift", 0.0)),
                "position_qualities": after.get(
                    "position_qualities", before.get("position_qualities", [])
                ),
            }
        )
    return result


def aggregate_citations(runs: list[NormalizedRun], company_name: str) -> dict[str, Any]:
    coverage: dict[str, int] = defaultdict(int)
    occurrences: dict[str, int] = defaultdict(int)
    domains: dict[str, str] = {}
    for run in runs:
        if not run.is_valid:
            continue
        for url, item in run_url_stats(run, company_name).items():
            coverage[url] += int(item["answer_coverage"])
            occurrences[url] += int(item["raw_occurrences"])
            domains[url] = str(item["domain"])
    leaders: list[dict[str, Any]] = list(
        (
            {
                "url": url,
                "domain": domains[url],
                "total_answer_coverage": count,
                "total_raw_occurrences": occurrences[url],
            }
            for url, count in coverage.items()
        )
    )
    leaders.sort(
        key=lambda item: (
            -int(str(item["total_answer_coverage"])),
            str(item["url"]),
        )
    )
    return {
        "distinct_url_count": len(leaders),
        "top_urls": leaders[:25],
        "counting_method": {
            "answer_coverage": "one unit per distinct answer/URL pair",
            "raw_occurrences": "all stored URL occurrences",
        },
    }
