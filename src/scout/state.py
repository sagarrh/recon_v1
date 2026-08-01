# state.py — LangGraph StateGraph schema for the Scout pipeline.
# Purpose: Defines the ScoutState TypedDict carried between every node in graph.py.
# Scope: Declarative type definitions only; no other logic, no side effects on import.
# Consumers: scout/graph.py builds the StateGraph from this schema; every node reads/writes its fields.
from typing import TypedDict

from scout.models.blog import BlogDetectionResult
from scout.models.history import ClusterHistory
from scout.models.investigation import (
    AICitationChange,
    CitationInvestigationEvidence,
    ClientReadiness,
    ThirdPartySignals,
    WebsiteChanges,
)
from scout.models.recommendation import ClientSummary, InternalReport, Recommendation
from scout.models.sov import ClusterVerdict, CycleSummary, InvestigationTrigger, SOVTrackingRecord


class ScoutState(TypedDict):
    run_id: str
    sync_date: str
    clients: list[dict]
    sov_tracking_records: list[SOVTrackingRecord]
    investigation_triggers: list[InvestigationTrigger]
    cluster_verdicts: list[ClusterVerdict]
    triage_digest: list[dict]
    website_changes: dict[str, WebsiteChanges]
    third_party_signals: dict[str, ThirdPartySignals]
    ai_citation_changes: dict[str, AICitationChange | CitationInvestigationEvidence]
    citation_bundle: dict
    client_readiness: dict[str, ClientReadiness]
    historical_context: dict[str, ClusterHistory]   # DH1-2: per-cluster multi-week history, keyed client_id::cluster_id
    recommendations: list[Recommendation]
    internal_reports: list[InternalReport]
    client_summaries: list[ClientSummary]
    cycle_summary: CycleSummary | None
    blog_detections: dict[str, BlogDetectionResult]
    blog_investigation_triggers: list[InvestigationTrigger]
    numeric_provenance_metric: dict   # R2-4: per-(node, model) hallucinated-number rate, written by validation_gate
