from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

from ai_visibility.analysis.change_points import detect_change_points
from ai_visibility.analysis.citations import aggregate_citations
from ai_visibility.analysis.comparisons import adjacent_comparisons, material_comparisons
from ai_visibility.analysis.timelines import (
    aggregate_visibility_timeline,
    partition_valid_runs,
    provider_intelligence,
    query_intelligence,
    trend_status,
)
from ai_visibility.attribution.hypotheses import choose_hypothesis
from ai_visibility.attribution.scoring import classify_url_relationship, score_page_candidate
from ai_visibility.companies.aliases import aliases_for
from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.reports.models import (
    AnalysisPeriod,
    CompanyIntelligenceReport,
    CompanySection,
)
from ai_visibility.reports.validation import validate_report_schema
from ai_visibility.utils.text import normalize_name


def _official_domains(
    runs: list[NormalizedRun],
    company_name: str,
    configured_domains: list[str] | None = None,
) -> list[str]:
    compact_name = normalize_name(company_name).replace(" ", "")
    domains = {
        citation.domain.removeprefix("www.")
        for run in runs
        for answer in run.answers
        for citation in answer.citations
        if compact_name
        and (
            citation.domain.removeprefix("www.") == f"{compact_name}.com"
            or citation.domain.removeprefix("www.").startswith(f"{compact_name}.")
        )
    }
    domains.update(domain.casefold().removeprefix("www.") for domain in configured_domains or [])
    return sorted(domains)


def _cluster_intelligence(
    query_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query in query_sections:
        key = str(query.get("cluster_name") or query.get("cluster_id") or "unclustered")
        grouped[key].append(query)
    return [
        {
            "cluster": cluster,
            "query_count": len(items),
            "current_average_visibility": float(
                np.mean([float(item["current_visibility"]) for item in items])
            ),
            "providers": sorted({str(item["provider"]) for item in items}),
        }
        for cluster, items in sorted(grouped.items())
    ]


def _build_signal(
    comparison: dict[str, Any],
    company_name: str,
    official_domains: list[str],
    page_evidence_by_url: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    direction = "increase" if float(comparison["literal_visibility_delta"]) > 0 else "decrease"
    source_evidence: list[dict[str, Any]] = []
    for delta in sorted(
        comparison["citation_deltas"],
        key=lambda item: (-abs(int(item["coverage_delta"])), item["url"]),
    )[:15]:
        page_evidence = page_evidence_by_url.get(str(delta["url"]), {})
        historical_snapshot_available = bool(page_evidence.get("change_summary"))
        change_summary = page_evidence.get("change_summary")
        mention_changes = (
            change_summary.get("company_mention_changes", [])
            if isinstance(change_summary, dict)
            else []
        )
        extraction_quality = page_evidence.get("extraction_quality")
        extraction_usable = (
            not isinstance(extraction_quality, dict) or extraction_quality.get("usable") is True
        )
        company_strengthened = any(
            normalize_name(str(change.get("company", ""))) == normalize_name(company_name)
            and int(change.get("delta", 0)) > 0
            for change in mention_changes
        )
        temporally_aligned = False
        try:
            previous_snapshot_at = datetime.fromisoformat(
                str(page_evidence["previous_snapshot_at"])
            )
            current_snapshot_at = datetime.fromisoformat(str(page_evidence["current_snapshot_at"]))
            previous_run_at = datetime.fromisoformat(str(comparison["previous_at"]))
            current_run_at = datetime.fromisoformat(str(comparison["current_at"]))
            temporally_aligned = (
                previous_snapshot_at <= previous_run_at and current_snapshot_at >= current_run_at
            )
        except (KeyError, TypeError, ValueError):
            temporally_aligned = False
        page_diff_strengthened = (
            company_strengthened
            and temporally_aligned
            and extraction_usable
            and isinstance(change_summary, dict)
            and change_summary.get("extraction_quality_usable") is True
        )
        relationship = classify_url_relationship(delta, int(comparison["current_answer_count"]))
        is_owned = str(delta.get("domain", "")).removeprefix("www.") in official_domains or bool(
            page_evidence.get("publisher_is_company")
        )
        scoring_input = {
            "status": delta["status"],
            "coverage_delta": delta["coverage_delta"],
            "association_lift": delta["current_association_lift"],
            "current_answer_coverage": delta["current_answer_coverage"],
            "current_answer_count": comparison["current_answer_count"],
            "publisher_is_company": is_owned,
            "page_mentions_company": (
                int(page_evidence.get("mention_count") or 0) > 0
                if page_evidence and extraction_usable
                else None
            ),
            "company_in_heading": bool(
                page_evidence.get("mentioned_in_title") or page_evidence.get("mentioned_in_heading")
            )
            if extraction_usable
            else False,
            "links_to_official_domain": (
                bool(page_evidence.get("links_to_official_domain")) if extraction_usable else False
            ),
            "historical_snapshot_available": historical_snapshot_available,
            "page_diff_strengthened": page_diff_strengthened,
            "positions_unavailable": any(
                quality in {"zeroed", "unavailable"}
                for quality in delta.get("position_qualities", [])
            ),
            "configuration_incomplete": bool(comparison["comparability_flags"]),
            "metric_mismatch": bool(comparison.get("metric_difference")),
        }
        score = score_page_candidate(scoring_input)
        source_evidence.append(
            {
                **delta,
                "is_client_owned": is_owned,
                "relationship": relationship,
                "attribution": score,
                "overall_contribution": (
                    "low" if relationship == "localized_new_owned_source" else score["confidence"]
                ),
                "historical_snapshot_available": historical_snapshot_available,
                "page_verification": page_evidence or None,
                "page_diff_strengthened": page_diff_strengthened,
                "snapshot_temporally_aligned": temporally_aligned,
                "warning": (
                    None if historical_snapshot_available else "historical_page_version_unavailable"
                ),
            }
        )
    hypothesis = choose_hypothesis({**comparison, "page_source_evidence": source_evidence})
    observed = {
        "company": company_name,
        "provider": comparison["provider"],
        "query": comparison["query"],
        "previous_run_id": comparison["previous_run_id"],
        "current_run_id": comparison["current_run_id"],
        "previous_literal_visibility": comparison["previous_literal_visibility"],
        "current_literal_visibility": comparison["current_literal_visibility"],
        "literal_visibility_delta": comparison["literal_visibility_delta"],
        "previous_answer_count": comparison["previous_answer_count"],
        "current_answer_count": comparison["current_answer_count"],
        "previous_literal_answer_count": comparison["previous_literal_answer_count"],
        "current_literal_answer_count": comparison["current_literal_answer_count"],
        "metric_difference": comparison["metric_difference"],
    }
    warnings = sorted(
        set(
            comparison["comparability_flags"]
            + (
                ["company_metric_mismatch"]
                if comparison.get("metric_difference") not in {None, 0}
                else []
            )
            + (
                ["historical_page_version_unavailable"]
                if any(not source["historical_snapshot_available"] for source in source_evidence)
                else []
            )
        )
    )
    direct_page_confidences = [
        source["attribution"]["confidence"]
        for source in source_evidence
        if source["page_diff_strengthened"]
    ]
    return {
        "signal_type": f"company_visibility_{direction}",
        "observed": observed,
        "primary_hypothesis": hypothesis,
        "confidence": hypothesis["confidence"],
        "evidence": {
            "recommendation_pattern": comparison.get("recommendation_pattern"),
            "sources": source_evidence,
            "competitor_deltas": comparison["competitor_deltas"][:10],
        },
        "single_page_causation_confidence": (
            max(
                direct_page_confidences,
                key=lambda value: {"low": 0, "medium": 1, "high": 2}[str(value)],
            )
            if direct_page_confidences
            else "low"
        ),
        "new_owned_article_overall_contribution": (
            "low"
            if any(
                source["relationship"] == "localized_new_owned_source" for source in source_evidence
            )
            else "unknown"
        ),
        "alternative_explanations": [
            "provider model or retrieval behavior changed",
            "execution configuration changed but was not fully captured",
            "provider volatility or source weighting changed",
            "competitor recommendation bundles changed",
        ],
        "recommended_actions": [
            "Track the changed query on the same provider and configuration to test persistence.",
            "Strengthen owned pages that directly answer the monitored query and name the company.",
            "Pursue relevant third-party comparison and recommendation coverage.",
            "Capture future page snapshots before making page-change claims.",
        ],
        "warnings": warnings,
    }


def build_report(
    runs: list[NormalizedRun],
    company_name: str,
    *,
    client_id: str,
    page_evidence: list[dict[str, Any]] | None = None,
    configured_official_domains: list[str] | None = None,
) -> CompanyIntelligenceReport:
    valid_runs = [run for run in runs if run.is_valid]
    invalid_runs = [run for run in runs if not run.is_valid]
    if not valid_runs:
        raise ValueError(f"No valid monitoring runs are available for {company_name!r}.")
    valid_runs.sort(key=lambda run: (run.created_at, str(run.run_id)))
    histories = partition_valid_runs(runs)
    comparisons = adjacent_comparisons(histories, company_name)
    material = material_comparisons(comparisons)
    providers = provider_intelligence(histories, company_name)
    queries = query_intelligence(histories, company_name)
    official_domains = _official_domains(
        valid_runs,
        company_name,
        configured_official_domains,
    )
    page_evidence = page_evidence or []
    page_evidence_by_url = {
        str(item["normalized_url"]): item for item in page_evidence if item.get("normalized_url")
    }
    signals = [
        _build_signal(
            item,
            company_name,
            official_domains,
            page_evidence_by_url,
        )
        for item in material
    ]
    target = normalize_name(company_name)

    latest_by_query = [history[-1] for history in histories.values()]
    latest_counts = [
        run.company_metrics[target].literal_answer_count
        for run in latest_by_query
        if target in run.company_metrics
    ]
    latest_totals = [len(run.answers) for run in latest_by_query if target in run.company_metrics]
    weighted_visibility = sum(latest_counts) / sum(latest_totals) if sum(latest_totals) else 0.0
    latest_unweighted = [
        run.company_metrics[target].literal_visibility
        for run in latest_by_query
        if target in run.company_metrics
    ]

    all_competitors: dict[str, dict[str, Any]] = {}
    for comparison in comparisons:
        for competitor in comparison["competitor_deltas"]:
            existing = all_competitors.get(competitor["company"])
            if existing is None or abs(competitor["visibility_delta"]) > abs(
                existing["visibility_delta"]
            ):
                all_competitors[competitor["company"]] = {
                    **competitor,
                    "provider": comparison["provider"],
                    "query": comparison["query"],
                }
    competitor_intelligence = sorted(
        all_competitors.values(),
        key=lambda item: (-abs(float(item["visibility_delta"])), item["company"]),
    )

    data_quality_flag_set = {flag for run in runs for flag in run.data_quality_flags}
    if not any(item.get("change_summary") for item in page_evidence):
        data_quality_flag_set.add("historical_page_version_unavailable")
    data_quality_flags = sorted(data_quality_flag_set)
    largest_positive = max(
        comparisons, key=lambda item: float(item["literal_visibility_delta"]), default=None
    )
    largest_negative = min(
        comparisons, key=lambda item: float(item["literal_visibility_delta"]), default=None
    )
    primary_signal = max(
        signals,
        key=lambda item: abs(float(item["observed"]["literal_visibility_delta"])),
        default=None,
    )
    provider_values = {item["provider"]: float(item["latest_visibility"]) for item in providers}
    strongest_provider = (
        max(provider_values, key=provider_values.get) if provider_values else None  # type: ignore[arg-type]
    )
    weakest_provider = (
        min(provider_values, key=provider_values.get) if provider_values else None  # type: ignore[arg-type]
    )

    change_timeline = [
        {
            "at": item["current_at"],
            "provider": item["provider"],
            "query": item["query"],
            "literal_visibility_delta": item["literal_visibility_delta"],
            "classification": (
                item["recommendation_pattern"]["signal_type"]
                if item.get("recommendation_pattern")
                else "material_visibility_change"
            ),
        }
        for item in material
    ]
    for query in queries:
        points = detect_change_points(
            [float(point["literal_visibility"]) for point in query["history"]]
        )
        query["change_points"] = [
            {**point, "run": query["history"][int(point["index"])]} for point in points
        ]

    overall_timeline = aggregate_visibility_timeline(histories, company_name)
    report = CompanyIntelligenceReport(
        company=CompanySection(
            canonical_name=company_name,
            client_id=client_id,
            official_domains=official_domains,
            aliases=aliases_for(company_name),
            competitor_count=len(competitor_intelligence),
        ),
        analysis_period=AnalysisPeriod(
            start=valid_runs[0].created_at.isoformat(),
            end=valid_runs[-1].created_at.isoformat(),
            valid_run_count=len(valid_runs),
            invalid_run_count=len(invalid_runs),
            provider_count=len({run.service for run in valid_runs}),
            query_count=len(histories),
            cluster_count=len(
                {
                    run.cluster_id or run.cluster_name
                    for run in valid_runs
                    if run.cluster_id or run.cluster_name
                }
            ),
        ),
        executive_summary={
            "overall_direction": trend_status(
                [float(point["visibility"]) for point in overall_timeline]
            ),
            "latest_weighted_visibility": weighted_visibility,
            "largest_positive_change": largest_positive,
            "largest_negative_change": largest_negative,
            "strongest_provider": strongest_provider,
            "weakest_provider": weakest_provider,
            "primary_hypothesis": (
                primary_signal["primary_hypothesis"] if primary_signal else None
            ),
            "confidence": primary_signal["confidence"] if primary_signal else "low",
        },
        overall_visibility={
            "latest_weighted_visibility": weighted_visibility,
            "latest_unweighted_visibility": (
                float(np.mean(latest_unweighted)) if latest_unweighted else 0.0
            ),
            "latest_literal_answer_count": sum(latest_counts),
            "latest_total_answer_count": sum(latest_totals),
            "history": overall_timeline,
        },
        provider_intelligence=providers,
        query_intelligence=queries,
        cluster_intelligence=_cluster_intelligence(queries),
        mention_quality={
            "metric": "boundary-aware literal alias presence per distinct answer",
            "upstream_metrics_preserved": True,
            "mismatch_run_count": sum(
                1
                for run in valid_runs
                if target in run.company_metrics
                and run.company_metrics[target].metric_difference not in {None, 0}
            ),
        },
        citation_intelligence={
            **aggregate_citations(valid_runs, company_name),
            "material_comparison_url_deltas": [
                {
                    **delta,
                    "relationship": classify_url_relationship(
                        delta, int(comparison["current_answer_count"])
                    ),
                }
                for comparison in material
                for delta in comparison["citation_deltas"]
                if abs(int(delta["coverage_delta"])) >= 1
            ],
        },
        page_intelligence={
            "status": (
                "verified_snapshots_available"
                if page_evidence
                else "no_selective_snapshots_available"
            ),
            "snapshot_count": len(page_evidence),
            "snapshots": page_evidence,
            "historical_snapshot_limitation": (
                None
                if any(item.get("change_summary") for item in page_evidence)
                else "historical_page_version_unavailable"
            ),
            "causal_policy": (
                "No page is labeled a direct driver without a historical diff or "
                "equivalently strong independent evidence."
            ),
        },
        competitor_intelligence=competitor_intelligence,
        change_timeline=change_timeline,
        signals=signals,
        recommended_actions=(
            primary_signal["recommended_actions"]
            if primary_signal
            else [
                "Continue monitoring with complete execution configuration metadata.",
                "Capture page snapshots for future attribution.",
            ]
        ),
        data_quality_flags=data_quality_flags,
        methodology={
            "source": "public.ai_monitoring (immutable, read-only)",
            "history_scope": "all valid runs for the resolved client",
            "provider_isolation": True,
            "failed_run_policy": "zero-answer runs excluded from baselines",
            "company_metric": (
                "distinct answers containing a boundary-aware canonical alias / total valid answers"
            ),
            "citation_counting": {
                "answer_coverage": "distinct answer/URL pairs",
                "raw_occurrences": "all URL occurrences",
            },
            "attribution_policy": (
                "co-occurrence is association, not causation; deterministic scores "
                "retain every component"
            ),
            "pipeline_version": "0.1.0",
        },
    )
    validate_report_schema(report.model_dump(mode="json"))
    return report
