from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompanySection(BaseModel):
    model_config = ConfigDict(extra="allow")

    canonical_name: str
    client_id: UUID
    official_domains: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    competitor_count: int = 0


class AnalysisPeriod(BaseModel):
    model_config = ConfigDict(extra="allow")

    start: datetime | None = None
    end: datetime | None = None
    valid_run_count: int = 0
    invalid_run_count: int = 0
    provider_count: int = 0
    query_count: int = 0
    cluster_count: int = 0


class ProviderIntelligenceSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str
    run_count: int = Field(ge=0)
    query_count: int = Field(ge=0)
    latest_visibility: float = Field(ge=0, le=1)
    average_visibility: float = Field(ge=0, le=1)
    trend: str
    volatility: float = Field(ge=0)


class QueryIntelligenceSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    monitor_query_key: str
    query: str
    provider: str
    method: str
    run_count: int = Field(ge=0)
    current_visibility: float = Field(ge=0, le=1)
    trend: str
    history: list[dict[str, Any]]


class CompetitorIntelligenceSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    company: str
    status: str
    previous_mentions: int = Field(ge=0)
    current_mentions: int = Field(ge=0)
    previous_visibility: float = Field(ge=0)
    current_visibility: float = Field(ge=0)
    visibility_delta: float


class SignalObservedSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    company: str
    provider: str
    query: str
    previous_run_id: UUID
    current_run_id: UUID
    previous_literal_visibility: float = Field(ge=0, le=1)
    current_literal_visibility: float = Field(ge=0, le=1)
    literal_visibility_delta: float = Field(ge=-1, le=1)


class HypothesisSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    confidence: Literal["low", "medium", "high"]
    inference: str


class SignalSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    signal_type: str
    observed: SignalObservedSection
    primary_hypothesis: HypothesisSection
    confidence: Literal["low", "medium", "high"]
    evidence: dict[str, Any]
    alternative_explanations: list[str]
    recommended_actions: list[str]
    warnings: list[str]


class CompanyIntelligenceReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_type: Literal["company_intelligence_report"] = "company_intelligence_report"
    company: CompanySection
    analysis_period: AnalysisPeriod
    executive_summary: dict[str, Any]
    overall_visibility: dict[str, Any] = Field(default_factory=dict)
    provider_intelligence: list[ProviderIntelligenceSection]
    query_intelligence: list[QueryIntelligenceSection]
    cluster_intelligence: list[dict[str, Any]] = Field(default_factory=list)
    mention_quality: dict[str, Any] = Field(default_factory=dict)
    citation_intelligence: dict[str, Any] = Field(default_factory=dict)
    page_intelligence: dict[str, Any] = Field(default_factory=dict)
    competitor_intelligence: list[CompetitorIntelligenceSection]
    change_timeline: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[SignalSection]
    recommended_actions: list[str | dict[str, Any]]
    data_quality_flags: list[str | dict[str, Any]]
    methodology: dict[str, Any]
