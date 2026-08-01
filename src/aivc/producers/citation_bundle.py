from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ai_visibility.analysis.comparisons import adjacent_comparisons
from ai_visibility.analysis.timelines import partition_valid_runs
from ai_visibility.config.settings import Settings
from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.pipeline import GenerationResult, generate_company_report
from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    ClientIdentity,
    EvidenceArtifact,
    MeasuredMetric,
    ProducerIdentity,
    ReportReference,
    Signal,
    SignalBundle,
    stable_id,
)
from aivc.contracts.validation import validate_bundle_schema


def _direction(delta: float) -> str:
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "stable"


def _comparison_signals(
    comparison: dict[str, Any],
    *,
    client_id: UUID,
    company_name: str,
    cluster_id: str | None,
    cluster_label: str | None,
) -> tuple[list[Signal], EvidenceArtifact]:
    comparison_key = stable_id(
        "citation-comparison",
        client_id,
        comparison["monitor_query_key"],
        comparison["previous_run_id"],
        comparison["current_run_id"],
    )
    evidence_id = f"citation-comparison:{comparison_key}"
    evidence = EvidenceArtifact(
        evidence_id=evidence_id,
        kind="citation.adjacent_run_comparison",
        source_ref=f"ai_monitoring:{comparison['current_run_id']}",
        observed_at=comparison["current_at"],
        payload=comparison,
    )
    common = {
        "cluster_id": cluster_id,
        "cluster_label": cluster_label,
        "monitor_query_key": comparison["monitor_query_key"],
        "provider": comparison["provider"],
        "query": comparison["query"],
        "previous_observed_at": comparison["previous_at"],
        "current_observed_at": comparison["current_at"],
        "evidence_refs": [evidence_id],
        "warnings": list(comparison["comparability_flags"]),
    }
    delta = float(comparison["literal_visibility_delta"])
    signals = [
        Signal(
            signal_id=stable_id(comparison_key, "target", company_name),
            signal_type="citation.visibility_change",
            subject_company=company_name,
            direction=_direction(delta),
            magnitude=MeasuredMetric(
                name="literal_visibility_delta",
                value=delta,
                unit="ratio",
                numerator=(
                    int(comparison["current_literal_answer_count"])
                    - int(comparison["previous_literal_answer_count"])
                ),
                denominator=int(comparison["current_answer_count"]),
            ),
            metrics=[
                MeasuredMetric(
                    name="previous_literal_visibility",
                    value=float(comparison["previous_literal_visibility"]),
                    unit="ratio",
                    numerator=int(comparison["previous_literal_answer_count"]),
                    denominator=int(comparison["previous_answer_count"]),
                ),
                MeasuredMetric(
                    name="current_literal_visibility",
                    value=float(comparison["current_literal_visibility"]),
                    unit="ratio",
                    numerator=int(comparison["current_literal_answer_count"]),
                    denominator=int(comparison["current_answer_count"]),
                ),
            ],
            confidence="medium" if comparison["comparability_flags"] else "high",
            source_payload={
                "previous_run_id": comparison["previous_run_id"],
                "current_run_id": comparison["current_run_id"],
                "metric_difference": comparison.get("metric_difference"),
            },
            **common,
        )
    ]
    for competitor in comparison["competitor_deltas"]:
        competitor_delta = float(competitor["visibility_delta"])
        signals.append(
            Signal(
                signal_id=stable_id(comparison_key, "competitor", competitor["company"]),
                signal_type="citation.competitor_visibility_change",
                subject_company=str(competitor["company"]),
                direction=_direction(competitor_delta),
                magnitude=MeasuredMetric(
                    name="literal_visibility_delta",
                    value=competitor_delta,
                    unit="ratio",
                    numerator=(
                        int(competitor["current_mentions"])
                        - int(competitor["previous_mentions"])
                    ),
                    denominator=int(comparison["current_answer_count"]),
                ),
                metrics=[
                    MeasuredMetric(
                        name="previous_literal_visibility",
                        value=float(competitor["previous_visibility"]),
                        unit="ratio",
                    ),
                    MeasuredMetric(
                        name="current_literal_visibility",
                        value=float(competitor["current_visibility"]),
                        unit="ratio",
                    ),
                ],
                confidence="medium" if comparison["comparability_flags"] else "high",
                source_payload={"status": competitor.get("status")},
                **common,
            )
        )
    return signals, evidence


def build_citation_bundle(
    runs: list[NormalizedRun] | tuple[NormalizedRun, ...],
    report: dict[str, Any],
    *,
    producer_version: str,
    producer_run_id: str,
    parent_run_id: UUID | None = None,
    report_id: str | None = None,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> SignalBundle:
    company = report["company"]
    company_name = str(company["canonical_name"])
    client_id = UUID(str(company["client_id"]))
    valid_runs = [run for run in runs if run.is_valid]
    histories = partition_valid_runs(list(runs))
    comparisons = adjacent_comparisons(histories, company_name)
    runs_by_id = {str(run.run_id): run for run in runs}
    signals: list[Signal] = []
    evidence: list[EvidenceArtifact] = []
    for comparison in comparisons:
        current = runs_by_id[str(comparison["current_run_id"])]
        produced, artifact = _comparison_signals(
            comparison,
            client_id=client_id,
            company_name=company_name,
            cluster_id=current.cluster_id,
            cluster_label=current.cluster_name,
        )
        signals.extend(produced)
        evidence.append(artifact)

    run_fingerprint = stable_id(*(str(run.run_id) for run in valid_runs))
    flags = [str(flag) for flag in report.get("data_quality_flags", []) if isinstance(flag, str)]
    status = BundleStatus.partial if flags else BundleStatus.complete
    bundle = SignalBundle(
        bundle_id=stable_id("ai_visibility", client_id, run_fingerprint, producer_version),
        created_at=datetime.now(UTC),
        producer=ProducerIdentity(
            name="ai_visibility",
            version=producer_version,
            run_id=producer_run_id,
            parent_run_id=parent_run_id,
        ),
        client=ClientIdentity(
            client_id=client_id,
            canonical_name=company_name,
            aliases=[str(item) for item in company.get("aliases", [])],
            official_domains=[str(item) for item in company.get("official_domains", [])],
        ),
        analysis_period=AnalysisPeriod(
            start=report["analysis_period"].get("start"),
            end=report["analysis_period"].get("end"),
        ),
        status=status,
        signals=signals,
        recommendations=[
            {"action": action} if isinstance(action, str) else action
            for action in report.get("recommended_actions", [])
        ],
        evidence=evidence,
        reports=[
            ReportReference(
                report_type="company_intelligence_report",
                report_id=report_id,
                json_path=str(json_path) if json_path else None,
                markdown_path=str(markdown_path) if markdown_path else None,
            )
        ],
        data_quality_flags=flags,
    ).sealed()
    bundle.verify_checksum()
    validate_bundle_schema(bundle.model_dump(mode="json"))
    return bundle


def write_bundle(path: Path, bundle: SignalBundle) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(bundle.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def generate_citation_bundle(
    settings: Settings, company_name: str
) -> tuple[GenerationResult, Path, SignalBundle]:
    result = generate_company_report(settings, company_name)
    bundle = build_citation_bundle(
        result.normalized_runs,
        result.report,
        producer_version=settings.pipeline_version,
        producer_run_id=str(result.report_id),
        report_id=str(result.report_id),
        json_path=result.json_path,
        markdown_path=result.markdown_path,
    )
    bundle_path = result.json_path.parent / "citation-signal-bundle.json"
    from aivc.database.orchestration import persist_signal_bundle

    persist_signal_bundle(settings, bundle)
    return result, write_bundle(bundle_path, bundle), bundle
