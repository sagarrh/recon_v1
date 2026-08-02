from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aivc.contracts.models import AnalysisPeriod, ClientIdentity
from aivc.reporting.config import ProfileSettings


class StrictReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinalReportStatus(StrEnum):
    complete = "complete"
    partial = "partial"
    blocked = "blocked"
    failed = "failed"


class MetricDefinition(StrictReportModel):
    metric_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    unit: Literal["count", "ratio", "percentage_points", "score", "usd", "unknown"]
    source_owner: Literal["ai_visibility", "scout", "aivc"]
    methodology_version: str = Field(min_length=1)
    aggregation_scope: str = Field(min_length=1)
    higher_is_better: bool | None = None


class ReportMetric(StrictReportModel):
    metric_name: str = Field(min_length=1)
    value: int | float | None
    unit: Literal["count", "ratio", "percentage_points", "score", "usd", "unknown"]
    numerator: int | float | None = None
    denominator: int | float | None = None
    previous_value: int | float | None = None
    delta: int | float | None = None
    observed_at: datetime | None = None
    provider: str | None = None
    query: str | None = None
    cluster_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


class MeasuredFact(StrictReportModel):
    statement: str = Field(min_length=1)
    metrics: list[ReportMetric] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class CitationSourceFinding(StrictReportModel):
    url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    status: str = Field(min_length=1)
    coverage_delta: int | float | None = None
    is_client_owned: bool = False
    historical_snapshot_available: bool = False
    evidence_ref: str


class RecommendedAction(StrictReportModel):
    action_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    category: str = Field(min_length=1)
    time_horizon: str | None = None
    measurement_signal: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)


class DecisionCard(StrictReportModel):
    card_id: str = Field(min_length=1)
    client_id: UUID
    cluster_id: str
    cluster_label: str
    provider: str | None = None
    query: str | None = None
    classification: Literal[
        "competitive_threat",
        "visibility_opportunity",
        "source_opportunity",
        "defensive_gap",
        "watch",
    ]
    priority: Literal["critical", "high", "medium", "low"]
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    measured_facts: list[MeasuredFact] = Field(min_length=1)
    client_metrics: list[ReportMetric] = Field(default_factory=list)
    competitor_metrics: list[ReportMetric] = Field(default_factory=list)
    citation_sources: list[CitationSourceFinding] = Field(default_factory=list)
    recon_evidence_summary: str | None = None
    confidence: Literal["high", "medium", "low", "unknown"]
    confidence_reasons: list[str] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    measurement_plan: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_must_resolve_locally(self) -> DecisionCard:
        fact_refs = {ref for fact in self.measured_facts for ref in fact.evidence_refs}
        if not fact_refs.issubset(set(self.evidence_refs)):
            raise ValueError("measured fact evidence must be included in card evidence_refs")
        return self


class ProviderPerformance(StrictReportModel):
    provider: str
    current_visibility: float | None = None
    trend: str
    query_count: int = Field(ge=0)
    metrics: list[ReportMetric] = Field(default_factory=list)


class SovCompanyPosition(StrictReportModel):
    company_name: str
    sov: float
    rank: int | None = None
    is_client: bool = False
    is_tracked: bool = False


class SovHistoryPoint(StrictReportModel):
    week_date: str
    company_name: str
    sov: float
    is_client: bool = False
    delta_pp: float | None = None
    alert_flag: str | None = None
    answers_analyzed: int | None = None
    platforms: list[str] = Field(default_factory=list)


class TopicPerformance(StrictReportModel):
    cluster_id: str
    cluster_label: str
    current_literal_visibility: float | None = None
    current_client_sov: float | None = None
    provider_count: int = Field(default=0, ge=0)
    query_count: int = Field(default=0, ge=0)
    as_of_week: str | None = None
    client_rank: int | None = None
    market_size: int = Field(default=0, ge=0)
    leader_name: str | None = None
    leader_sov: float | None = None
    gap_to_leader: float | None = None
    positions: list[SovCompanyPosition] = Field(default_factory=list)
    history: list[SovHistoryPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class QueryPerformance(StrictReportModel):
    monitor_query_key: str
    query: str
    provider: str
    method: str | None = None
    cluster_id: str | None = None
    cluster_name: str | None = None
    current_visibility: float | None = None
    latest_delta: float | None = None
    trend: str
    run_count: int = Field(default=0, ge=0)


class ReconSignalView(StrictReportModel):
    signal_id: str
    cluster_id: str | None = None
    cluster_name: str | None = None
    week_date: str | None = None
    severity: str | None = None
    competitor: str | None = None
    competitor_delta_pp: float | None = None
    evidence_summary: str | None = None


class ReconRecommendationView(StrictReportModel):
    recommendation_id: str
    cluster_id: str | None = None
    cluster_name: str | None = None
    competitor: str | None = None
    priority: str | None = None
    confidence: str | None = None
    summary: str | None = None
    probable_cause: str | None = None
    gap_analysis: str | None = None
    actions: list[str] = Field(default_factory=list)
    timeline: str | None = None


class ReconRunHistory(StrictReportModel):
    run_id: str
    status: str
    sync_date: str | None = None
    started_at: datetime | None = None
    triggers_fired: int = Field(default=0, ge=0)


class ExcludedTopic(StrictReportModel):
    cluster_id: str | None = None
    cluster_name: str
    reason: str


class ReconReportingPayload(StrictReportModel):
    """Complete, lossless result of the packaged Recon reporting SQL."""

    client: dict[str, Any]
    clusters: list[dict[str, Any]]
    sov: dict[str, Any]
    signals: list[dict[str, Any]]
    executive_summary: str | None = None
    recommendations: list[dict[str, Any]]
    run_history: list[dict[str, Any]]


class ClientHeadlineMetric(StrictReportModel):
    label: str
    value: str
    context: str


class ClientTopicBrief(StrictReportModel):
    priority_rank: int = Field(ge=1)
    cluster_id: str
    cluster_label: str
    status: str
    client_sov: float | None = None
    client_rank: int | None = None
    market_size: int = Field(default=0, ge=0)
    leader_name: str | None = None
    leader_sov: float | None = None
    gap_to_leader: float | None = None
    interpretation: str
    tracked_positions: list[SovCompanyPosition] = Field(default_factory=list)
    competitive_takeaway: str
    recommended_focus: list[str] = Field(default_factory=list)


class ClientPriority(StrictReportModel):
    priority_rank: int = Field(ge=1)
    source: Literal["citation", "recon"]
    title: str
    topic: str | None = None
    competitor: str | None = None
    priority: str
    confidence: str
    what_is_happening: str
    why_it_matters: str
    working_hypothesis: str | None = None
    actions: list[str] = Field(default_factory=list)
    timeline: str | None = None


class ClientActionGroup(StrictReportModel):
    horizon: Literal["Immediate", "Near term", "Monitor"]
    actions: list[str] = Field(default_factory=list)


class ClientPresentation(StrictReportModel):
    executive_narrative: str
    main_implication_title: str
    main_implication: str
    headline_metrics: list[ClientHeadlineMetric] = Field(default_factory=list)
    topics: list[ClientTopicBrief] = Field(default_factory=list)
    priorities: list[ClientPriority] = Field(default_factory=list)
    action_plan: list[ClientActionGroup] = Field(default_factory=list)
    leadership_decisions: list[str] = Field(default_factory=list)
    strategic_conclusion: str


class EvidenceIndexItem(StrictReportModel):
    evidence_id: str
    kind: str
    source_ref: str | None = None
    summary: str | None = None


class ArtifactRecord(StrictReportModel):
    artifact_type: Literal[
        "json",
        "markdown",
        "html",
        "manifest",
        "citation_input",
        "recon_input",
        "report_input",
        "report_content",
    ]
    path: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str
    generated_at: datetime


class ReportConfigMetadata(StrictReportModel):
    config_version: str
    report_profile: Literal["detailed"] = "detailed"
    report_audience: Literal["client"] = "client"
    report_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: str
    effective_profile: ProfileSettings


class FinalReportSnapshot(StrictReportModel):
    # 1.0 remains readable so historical reports can be upgraded in place.
    # New snapshots always use 1.2 and the current JSON Schema only accepts 1.2.
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    report_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    parent_run_id: UUID
    client: ClientIdentity
    config: ReportConfigMetadata
    source_bundle_ids: list[str] = Field(min_length=2)
    source_bundle_checksums: dict[str, str]
    analysis_period: AnalysisPeriod
    status: FinalReportStatus
    headline: str
    executive_summary: str
    executive_metrics: list[ReportMetric] = Field(default_factory=list)
    provider_summary: list[ProviderPerformance] = Field(default_factory=list)
    topic_summary: list[TopicPerformance] = Field(default_factory=list)
    query_details: list[QueryPerformance] = Field(default_factory=list)
    recon_signals: list[ReconSignalView] = Field(default_factory=list)
    recon_recommendations: list[ReconRecommendationView] = Field(default_factory=list)
    recon_run_history: list[ReconRunHistory] = Field(default_factory=list)
    excluded_topics: list[ExcludedTopic] = Field(default_factory=list)
    recon_reporting: ReconReportingPayload | None = None
    client_presentation: ClientPresentation | None = None
    decision_cards: list[DecisionCard] = Field(default_factory=list)
    consolidated_actions: list[RecommendedAction] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    methodology: list[MetricDefinition] = Field(default_factory=list)
    evidence_index: list[EvidenceIndexItem] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checksum: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"checksum", "generated_at"})

    def computed_checksum(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def sealed(self) -> FinalReportSnapshot:
        return self.model_copy(update={"checksum": self.computed_checksum()})

    def verify_checksum(self) -> None:
        if not self.checksum or self.checksum != self.computed_checksum():
            raise ValueError("final report snapshot checksum is missing or invalid")


class ArtifactManifest(StrictReportModel):
    report_id: str
    parent_run_id: UUID
    report_profile: Literal["detailed"] = "detailed"
    report_audience: Literal["client"] = "client"
    snapshot_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: list[ArtifactRecord]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
