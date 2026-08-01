from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from aivc.contracts.models import EvidenceArtifact, Signal, stable_id
from aivc.reporting.actions import consolidate_actions
from aivc.reporting.config import ProfileSettings
from aivc.reporting.correlation import correlate_signals
from aivc.reporting.models import (
    CitationSourceFinding,
    DecisionCard,
    MeasuredFact,
    ReportMetric,
)
from aivc.reporting.quality import PublicationResult


def _metric(signal: Signal, name: str) -> float | None:
    found = next((metric.value for metric in signal.metrics if metric.name == name), None)
    return None if found is None else float(found)


def _citation_metric(signal: Signal) -> ReportMetric:
    current = _metric(signal, "current_literal_visibility")
    previous = _metric(signal, "previous_literal_visibility")
    return ReportMetric(
        metric_name="provider_query_visibility",
        value=current,
        previous_value=previous,
        delta=float(signal.magnitude.value or 0.0) if signal.magnitude else None,
        unit="ratio",
        numerator=signal.magnitude.numerator if signal.magnitude else None,
        denominator=signal.magnitude.denominator if signal.magnitude else None,
        observed_at=signal.current_observed_at,
        provider=signal.provider,
        query=signal.query,
        cluster_id=signal.cluster_id,
        evidence_refs=list(signal.evidence_refs),
        quality_flags=list(signal.warnings),
    )


def _recon_metric(signal: Signal) -> ReportMetric:
    return ReportMetric(
        metric_name=signal.magnitude.name if signal.magnitude else "weekly_cluster_sov",
        value=signal.magnitude.value if signal.magnitude else None,
        unit="percentage_points",
        observed_at=signal.current_observed_at,
        cluster_id=signal.cluster_id,
        evidence_refs=list(signal.evidence_refs),
        quality_flags=list(signal.warnings),
    )


def _source_findings(
    signal: Signal,
    evidence: Mapping[str, EvidenceArtifact],
    limit: int,
) -> list[CitationSourceFinding]:
    findings: list[CitationSourceFinding] = []
    for evidence_ref in signal.evidence_refs:
        artifact = evidence.get(evidence_ref)
        if artifact is None:
            continue
        deltas = artifact.payload.get("citation_deltas", [])
        if not isinstance(deltas, list):
            continue
        ranked = sorted(
            (item for item in deltas if isinstance(item, dict)),
            key=lambda item: -abs(float(item.get("coverage_delta") or 0)),
        )
        for item in ranked[:limit]:
            url = str(item.get("url") or "")
            domain = str(item.get("domain") or "")
            if not url or not domain:
                continue
            findings.append(
                CitationSourceFinding(
                    url=url,
                    domain=domain,
                    status=str(item.get("status") or "changed"),
                    coverage_delta=item.get("coverage_delta"),
                    is_client_owned=bool(item.get("is_client_owned", False)),
                    historical_snapshot_available=bool(
                        item.get("historical_snapshot_available", False)
                    ),
                    evidence_ref=evidence_ref,
                )
            )
    return findings[:limit]


def _recommendation_candidates(
    recommendations: tuple[dict[str, object], ...], cluster_id: str
) -> list[tuple[str, str, str | None, str | None]]:
    candidates: list[tuple[str, str, str | None, str | None]] = []
    for recommendation in recommendations:
        if str(recommendation.get("cluster_id") or "") != cluster_id:
            continue
        source_id = str(
            recommendation.get("investigation_id")
            or stable_id("recon-recommendation", cluster_id, recommendation.get("summary"))
        )
        timeline = str(recommendation.get("timeline") or "") or None
        bullets = recommendation.get("action_bullets")
        if isinstance(bullets, list):
            candidates.extend(
                (str(action), source_id, timeline, None)
                for action in bullets
                if str(action).strip()
            )
    return candidates


def _priority(
    citation: Signal | None, recon: Signal | None
) -> Literal["critical", "high", "medium", "low"]:
    citation_delta = (
        abs(float(citation.magnitude.value or 0))
        if citation and citation.magnitude
        else 0
    )
    recon_delta = abs(float(recon.magnitude.value or 0)) if recon and recon.magnitude else 0
    client_decline = citation is not None and citation.direction == "decrease"
    if citation and recon and client_decline and (citation_delta >= 0.2 or recon_delta >= 5):
        return "critical"
    if citation_delta >= 0.2 or recon_delta >= 5:
        return "high"
    if citation_delta >= 0.1 or recon_delta > 0:
        return "medium"
    return "low"


def build_decision_cards(
    publication: PublicationResult,
    *,
    client_id: UUID,
    client_name: str,
    profile: ProfileSettings,
) -> list[DecisionCard]:
    cards: list[DecisionCard] = []
    for group in correlate_signals(publication.citation_signals, publication.recon_signals):
        citation = max(
            group.citation,
            key=lambda signal: abs(float(signal.magnitude.value or 0))
            if signal.magnitude
            else 0,
            default=None,
        )
        recon = max(
            group.recon,
            key=lambda signal: abs(float(signal.magnitude.value or 0))
            if signal.magnitude
            else 0,
            default=None,
        )
        if citation is None and recon is None:
            continue
        evidence_refs = sorted(
            {
                ref
                for signal in (citation, recon)
                if signal is not None
                for ref in signal.evidence_refs
            }
        )
        facts: list[MeasuredFact] = []
        client_metrics: list[ReportMetric] = []
        competitor_metrics: list[ReportMetric] = []
        if citation is not None:
            metric = _citation_metric(citation)
            client_metrics.append(metric)
            facts.append(
                MeasuredFact(
                    statement=(
                        f"{client_name} literal answer visibility {citation.direction}d on "
                        f"{citation.provider or 'the monitored provider'} for the measured query."
                    ),
                    metrics=[metric],
                    evidence_refs=list(citation.evidence_refs),
                )
            )
        if recon is not None:
            metric = _recon_metric(recon)
            competitor_metrics.append(metric)
            facts.append(
                MeasuredFact(
                    statement=(
                        f"{recon.subject_company} recorded a Recon SOV "
                        f"{recon.direction.replace('_', ' ')} on this topic."
                    ),
                    metrics=[metric],
                    evidence_refs=list(recon.evidence_refs),
                )
            )
        corroborated = citation is not None and recon is not None
        classification = (
            "competitive_threat"
            if recon is not None and recon.direction in {"increase", "new"}
            else "visibility_opportunity"
            if citation is not None
            else "watch"
        )
        cluster_label = (
            (citation.cluster_label if citation else None)
            or (recon.cluster_label if recon else None)
            or group.cluster_id
        )
        recon_summary = None
        matching_recommendation = next(
            (
                recommendation
                for recommendation in publication.recommendations
                if str(recommendation.get("cluster_id") or "") == group.cluster_id
                and (
                    recon is None
                    or str(recommendation.get("competitor_name") or "")
                    == recon.subject_company
                )
            ),
            None,
        )
        if matching_recommendation is not None:
            recon_summary = str(
                matching_recommendation.get("probable_cause")
                or matching_recommendation.get("summary")
                or ""
            ) or None
        if recon_summary is None and recon is not None and recon.evidence_refs:
            artifact = publication.evidence.get(recon.evidence_refs[0])
            if artifact is not None:
                recon_summary = str(
                    artifact.payload.get("triage_reason")
                    or artifact.payload.get("alert_reason")
                    or ""
                ) or None
        actions = consolidate_actions(
            _recommendation_candidates(publication.recommendations, group.cluster_id),
            limit=profile.max_recommendations,
        )
        if not actions and citation is not None:
            actions = consolidate_actions(
                [
                    (
                        "Strengthen an authoritative page that directly answers the "
                        "monitored query.",
                        citation.signal_id,
                        "Next 30 days",
                        "Literal answer visibility and owned-source citation coverage",
                    )
                ],
                limit=profile.max_recommendations,
            )
        summary_parts = [fact.statement for fact in facts]
        card = DecisionCard(
            card_id=stable_id("decision-card", client_id, group.cluster_id, *evidence_refs),
            client_id=client_id,
            cluster_id=group.cluster_id,
            cluster_label=str(cluster_label),
            provider=citation.provider if citation else None,
            query=citation.query if citation else None,
            classification=classification,
            priority=_priority(citation, recon),
            title=f"{cluster_label}: measured visibility signal",
            summary=" ".join(summary_parts),
            measured_facts=facts,
            client_metrics=client_metrics,
            competitor_metrics=competitor_metrics,
            citation_sources=(
                _source_findings(citation, publication.evidence, profile.max_sources_per_card)
                if citation
                else []
            ),
            recon_evidence_summary=recon_summary,
            confidence="high" if corroborated else "medium",
            confidence_reasons=(
                ["Recon and citation evidence are aligned to the same exact cluster."]
                if corroborated
                else ["The finding currently has one material evidence stream."]
            ),
            recommended_actions=actions,
            measurement_plan=[
                "Repeat the same provider/query and cluster measurements in the next cycle."
            ],
            alternative_explanations=[
                "Provider retrieval or source weighting may have changed.",
                "The observed movement may be temporary answer variability.",
            ],
            evidence_refs=evidence_refs,
            warnings=sorted(
                {
                    warning
                    for signal in (citation, recon)
                    if signal is not None
                    for warning in signal.warnings
                }
            ),
        )
        cards.append(card)
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    cards.sort(key=lambda card: (priority_order[card.priority], card.cluster_label, card.card_id))
    return cards[: profile.max_decision_cards]
