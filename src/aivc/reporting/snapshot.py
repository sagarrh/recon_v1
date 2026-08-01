from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from aivc.contracts.models import SignalBundle, stable_id
from aivc.reporting.cards import build_decision_cards
from aivc.reporting.config import ResolvedReportConfig
from aivc.reporting.metrics import metric_definitions
from aivc.reporting.models import (
    EvidenceIndexItem,
    FinalReportSnapshot,
    FinalReportStatus,
    ProviderPerformance,
    ReportConfigMetadata,
    ReportMetric,
    TopicPerformance,
)
from aivc.reporting.quality import assess_publication

_LIMITATION_TEXT = {
    "citation_positions_unavailable": (
        "Some providers did not supply usable citation offsets, so citation position is "
        "not compared."
    ),
    "company_metric_mismatch": (
        "Literal company counts differ from an upstream metric in at least one run; both "
        "remain auditable."
    ),
    "execution_configuration_incomplete": (
        "Some provider execution settings were incomplete, reducing comparison confidence."
    ),
    "failed_monitoring_run": (
        "Zero-answer monitoring runs were retained but excluded from baselines."
    ),
    "historical_page_version_unavailable": (
        "Historical page versions are unavailable for some sources, so page causation "
        "is not claimed."
    ),
}


def _provider_summary(
    citation_report: dict[str, Any], config: ResolvedReportConfig
) -> list[ProviderPerformance]:
    rows = citation_report.get("provider_intelligence", [])
    if not isinstance(rows, list):
        return []
    result: list[ProviderPerformance] = []
    for row in rows[: config.profile_settings.max_provider_rows]:
        if not isinstance(row, dict):
            continue
        current = row.get("latest_visibility")
        result.append(
            ProviderPerformance(
                provider=str(row.get("provider") or "unknown"),
                current_visibility=float(current) if current is not None else None,
                trend=str(row.get("trend") or "unknown"),
                query_count=int(row.get("query_count") or 0),
                metrics=[
                    ReportMetric(
                        metric_name="literal_answer_visibility",
                        value=float(current) if current is not None else None,
                        unit="ratio",
                        numerator=row.get("latest_literal_answer_count"),
                        denominator=row.get("latest_total_answer_count"),
                        provider=str(row.get("provider") or "unknown"),
                    )
                ],
            )
        )
    return result


def _executive_metrics(citation_report: dict[str, Any]) -> list[ReportMetric]:
    overall = citation_report.get("overall_visibility", {})
    if not isinstance(overall, dict):
        return []
    value = overall.get("latest_weighted_visibility")
    return [
        ReportMetric(
            metric_name="literal_answer_visibility",
            value=float(value) if value is not None else None,
            unit="ratio",
            numerator=overall.get("latest_literal_answer_count"),
            denominator=overall.get("latest_total_answer_count"),
            quality_flags=["latest_query_cohort"],
        )
    ]


def _topic_summary(cards: list[Any]) -> list[TopicPerformance]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for card in cards:
        grouped[card.cluster_id].append(card)
    rows: list[TopicPerformance] = []
    for cluster_id, items in sorted(grouped.items()):
        literal_values = [
            metric.value
            for card in items
            for metric in card.client_metrics
            if metric.metric_name == "provider_query_visibility" and metric.value is not None
        ]
        rows.append(
            TopicPerformance(
                cluster_id=cluster_id,
                cluster_label=items[0].cluster_label,
                current_literal_visibility=(
                    sum(float(value) for value in literal_values) / len(literal_values)
                    if literal_values
                    else None
                ),
                # Recon signals in a decision card describe a competitor, not the client.
                current_client_sov=None,
                provider_count=len({card.provider for card in items if card.provider}),
                query_count=len({card.query for card in items if card.query}),
                warnings=sorted({warning for card in items for warning in card.warnings}),
            )
        )
    return rows


def build_final_report_snapshot(
    *,
    parent_run_id: UUID,
    citation_report: dict[str, Any],
    citation_bundle: SignalBundle,
    recon_bundle: SignalBundle,
    combined_bundle: SignalBundle,
    config: ResolvedReportConfig,
) -> FinalReportSnapshot:
    publication = assess_publication(citation_bundle, recon_bundle, combined_bundle)
    client = combined_bundle.client
    cards = build_decision_cards(
        publication,
        client_id=client.client_id,
        client_name=client.canonical_name,
        profile=config.profile_settings,
    )
    consolidated = []
    seen_actions: set[str] = set()
    for card in cards:
        for action in card.recommended_actions:
            if action.action_id not in seen_actions:
                consolidated.append(action)
                seen_actions.add(action.action_id)
            if len(consolidated) >= config.profile_settings.max_recommendations:
                break
        if len(consolidated) >= config.profile_settings.max_recommendations:
            break

    flags = sorted(set(publication.quality_flags))
    limitations = [_LIMITATION_TEXT.get(flag, flag.replace("_", " ")) for flag in flags]
    limitations.extend(blocker.replace("_", " ") for blocker in publication.blockers)
    status = (
        FinalReportStatus.blocked
        if publication.blockers
        else FinalReportStatus.partial
        if flags
        else FinalReportStatus.complete
    )
    headline = (
        f"{client.canonical_name}: {len(cards)} material AI visibility priorities"
        if cards
        else f"{client.canonical_name}: no material publishable movement detected"
    )
    executive_summary = (
        "The report combines exact-query citation evidence with Recon topic-level competitive "
        f"signals. {len(cards)} decision card(s) passed identity, materiality, and publication "
        "checks."
    )
    used_evidence = sorted({ref for card in cards for ref in card.evidence_refs})
    evidence_index = []
    for evidence_id in used_evidence:
        artifact = publication.evidence[evidence_id]
        payload = artifact.payload
        summary = str(
            payload.get("triage_reason")
            or payload.get("query")
            or payload.get("alert_reason")
            or ""
        ) or None
        evidence_index.append(
            EvidenceIndexItem(
                evidence_id=evidence_id,
                kind=artifact.kind,
                source_ref=artifact.source_ref,
                summary=summary,
            )
        )

    source_checksums = {
        citation_bundle.bundle_id: str(citation_bundle.checksum),
        recon_bundle.bundle_id: str(recon_bundle.checksum),
        combined_bundle.bundle_id: str(combined_bundle.checksum),
    }
    input_checksum = stable_id(*sorted(source_checksums.values()))
    idempotency_key = stable_id(
        parent_run_id,
        config.profile,
        config.config_hash,
        input_checksum,
        "1.0",
    )
    snapshot = FinalReportSnapshot(
        report_id=stable_id("final-report", idempotency_key),
        idempotency_key=idempotency_key,
        parent_run_id=parent_run_id,
        client=client,
        config=ReportConfigMetadata(
            config_version=config.config.config_version,
            report_profile=config.profile,
            report_config_hash=config.config_hash,
            source=config.source,
            effective_profile=config.profile_settings,
        ),
        source_bundle_ids=[citation_bundle.bundle_id, recon_bundle.bundle_id],
        source_bundle_checksums=source_checksums,
        analysis_period=combined_bundle.analysis_period,
        status=status,
        headline=headline,
        executive_summary=executive_summary,
        executive_metrics=_executive_metrics(citation_report),
        provider_summary=_provider_summary(citation_report, config),
        topic_summary=_topic_summary(cards),
        decision_cards=cards,
        consolidated_actions=consolidated,
        data_quality_flags=flags + list(publication.blockers),
        limitations=limitations,
        methodology=metric_definitions(),
        evidence_index=evidence_index,
    ).sealed()
    snapshot.verify_checksum()
    return snapshot
