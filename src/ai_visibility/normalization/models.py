from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RawMonitoringRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    client_id: UUID
    cluster_id: str | None = None
    cluster_name: str | None = None
    request_payload: Any = Field(default_factory=dict)
    answers_list: Any = Field(default_factory=list)
    citations_list: Any = Field(default_factory=list)
    citations_data: Any = Field(default_factory=dict)
    companies_data: Any = Field(default_factory=dict)
    created_at: datetime


class NormalizedCitation(BaseModel):
    answer_number: int
    original_url: str
    normalized_url: str
    domain: str
    title: str | None = None
    raw_occurrence_count: int = 1
    start_index: int | None = None
    end_index: int | None = None
    position_quality: str = "unavailable"


class NormalizedAnswer(BaseModel):
    answer_number: int
    text: str
    answer_hash: str
    word_count: int
    citations: list[NormalizedCitation] = Field(default_factory=list)


class CompanyMetric(BaseModel):
    company_name: str
    aliases: list[str]
    literal_answer_count: int
    literal_answer_numbers: list[int]
    literal_visibility: float
    literal_total_mentions: int
    upstream_count: float | None = None
    upstream_visibility: float | None = None
    upstream_word_count: float | None = None
    metric_difference: float | None = None
    data_quality_flags: list[str] = Field(default_factory=list)


class NormalizedRun(BaseModel):
    run_id: UUID
    client_id: UUID
    created_at: datetime
    cluster_id: str | None
    cluster_name: str | None
    base_query: str
    normalized_base_query: str
    service: str
    method: str
    configuration_hash: str
    configuration_completeness: str
    monitor_query_key: str
    is_valid: bool
    invalid_reason: str | None = None
    answers: list[NormalizedAnswer] = Field(default_factory=list)
    company_metrics: dict[str, CompanyMetric] = Field(default_factory=dict)
    data_quality_flags: list[str] = Field(default_factory=list)
