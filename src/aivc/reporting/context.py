from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from aivc.contracts.models import AnalysisPeriod, ClientIdentity
from aivc.reporting.models import (
    ClientTopicBrief,
    DecisionCard,
    EvidenceIndexItem,
    FinalReportSnapshot,
    ProviderPerformance,
    QueryPerformance,
    RecommendedAction,
    ReconRecommendationView,
    ReconRunHistory,
    ReconSignalView,
    ReportMetric,
    StrictReportModel,
)


class CitationReportInput(StrictReportModel):
    """Compact, report-ready Citation facts; the full ledger stays in PostgreSQL."""

    source_bundle_id: str
    source_bundle_checksum: str
    executive_metrics: list[ReportMetric] = Field(default_factory=list)
    providers: list[ProviderPerformance] = Field(default_factory=list)
    queries: list[QueryPerformance] = Field(default_factory=list)
    material_findings: list[DecisionCard] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    evidence_index: list[EvidenceIndexItem] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


class ReconReportInput(StrictReportModel):
    """Compact, report-ready Recon facts selected from the read-only SQL result."""

    source_bundle_id: str
    source_bundle_checksum: str
    executive_summary: str
    topics: list[ClientTopicBrief] = Field(default_factory=list)
    signals: list[ReconSignalView] = Field(default_factory=list)
    recommendations: list[ReconRecommendationView] = Field(default_factory=list)
    recent_runs: list[ReconRunHistory] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


class ReportInputSnapshot(StrictReportModel):
    """Exact compact evidence envelope supplied to the report narrative model."""

    schema_version: Literal["1.0"] = "1.0"
    prompt_version: Literal["1.0"] = "1.0"
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_run_id: str
    client: ClientIdentity
    analysis_period: AnalysisPeriod
    citation: CitationReportInput
    recon: ReconReportInput
    quality_flags: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    checksum: str | None = None

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"checksum"})

    def computed_checksum(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def sealed(self) -> ReportInputSnapshot:
        return self.model_copy(update={"checksum": self.computed_checksum()})

    def verify_checksum(self) -> None:
        if not self.checksum or self.checksum != self.computed_checksum():
            raise ValueError("report input checksum is missing or invalid")


def _source(snapshot: FinalReportSnapshot, index: int) -> tuple[str, str]:
    try:
        bundle_id = snapshot.source_bundle_ids[index]
        return bundle_id, snapshot.source_bundle_checksums[bundle_id]
    except (IndexError, KeyError) as exc:
        raise ValueError("final report source bundle metadata is incomplete") from exc


def build_report_input(
    snapshot: FinalReportSnapshot,
    *,
    prompt_sha256: str,
) -> ReportInputSnapshot:
    """Reduce the validated snapshot to the facts useful for client report writing."""
    snapshot.verify_checksum()
    presentation = snapshot.client_presentation
    if presentation is None:
        raise ValueError("client presentation is required before report input reduction")
    citation_id, citation_checksum = _source(snapshot, 0)
    recon_id, recon_checksum = _source(snapshot, 1)
    citation_cards = [
        card
        for card in snapshot.decision_cards
        if card.provider is not None or card.citation_sources or card.client_metrics
    ]
    citation_action_ids = {
        action.action_id for card in citation_cards for action in card.recommended_actions
    }
    report_input = ReportInputSnapshot(
        prompt_sha256=prompt_sha256,
        parent_run_id=str(snapshot.parent_run_id),
        client=snapshot.client,
        analysis_period=snapshot.analysis_period,
        citation=CitationReportInput(
            source_bundle_id=citation_id,
            source_bundle_checksum=citation_checksum,
            executive_metrics=snapshot.executive_metrics,
            providers=snapshot.provider_summary,
            queries=snapshot.query_details,
            material_findings=citation_cards,
            recommended_actions=[
                action
                for action in snapshot.consolidated_actions
                if action.action_id in citation_action_ids
            ],
            evidence_index=snapshot.evidence_index,
            quality_flags=snapshot.data_quality_flags,
        ),
        recon=ReconReportInput(
            source_bundle_id=recon_id,
            source_bundle_checksum=recon_checksum,
            executive_summary=snapshot.executive_summary,
            topics=presentation.topics,
            signals=snapshot.recon_signals,
            recommendations=snapshot.recon_recommendations,
            recent_runs=snapshot.recon_run_history,
            quality_flags=snapshot.data_quality_flags,
        ),
        quality_flags=snapshot.data_quality_flags,
        limitations=snapshot.limitations,
    ).sealed()
    report_input.verify_checksum()
    return report_input
