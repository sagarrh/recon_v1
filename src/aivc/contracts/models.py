from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BundleStatus(StrEnum):
    complete = "complete"
    partial = "partial"
    failed = "failed"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientIdentity(ContractModel):
    client_id: UUID
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    official_domains: list[str] = Field(default_factory=list)


class AnalysisPeriod(ContractModel):
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def validate_order(self) -> AnalysisPeriod:
        if self.start and self.end and self.start > self.end:
            raise ValueError("analysis period start must not be after end")
        return self


class ProducerIdentity(ContractModel):
    name: Literal["ai_visibility", "scout"]
    version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    parent_run_id: UUID | None = None


class MeasuredMetric(ContractModel):
    name: str = Field(min_length=1)
    value: int | float | None
    unit: Literal[
        "count",
        "ratio",
        "percentage_points",
        "score",
        "seconds",
        "usd",
        "unknown",
    ]
    numerator: int | float | None = None
    denominator: int | float | None = None


class EvidenceArtifact(ContractModel):
    evidence_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    source_ref: str | None = None
    observed_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Signal(ContractModel):
    signal_id: str = Field(min_length=1)
    signal_type: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    subject_company: str = Field(min_length=1)
    cluster_id: str | None = None
    cluster_label: str | None = None
    monitor_query_key: str | None = None
    provider: str | None = None
    query: str | None = None
    previous_observed_at: datetime | None = None
    current_observed_at: datetime | None = None
    direction: Literal["increase", "decrease", "new", "removed", "stable", "unknown"]
    magnitude: MeasuredMetric | None = None
    metrics: list[MeasuredMetric] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high", "unknown"]
    evidence_refs: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_payload: dict[str, Any] = Field(default_factory=dict)


class ReportReference(ContractModel):
    report_type: str = Field(min_length=1)
    report_id: str | None = None
    json_path: str | None = None
    markdown_path: str | None = None


class SignalBundle(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str = Field(min_length=1)
    created_at: datetime
    producer: ProducerIdentity
    client: ClientIdentity
    analysis_period: AnalysisPeriod
    status: BundleStatus
    signals: list[Signal] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceArtifact] = Field(default_factory=list)
    reports: list[ReportReference] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)
    # Kept only so pre-1.3 source bundles remain readable. New source bundles leave it empty.
    source_bundle_ids: list[str] = Field(default_factory=list)
    checksum: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"checksum", "created_at"})
        return payload

    def computed_checksum(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def sealed(self) -> SignalBundle:
        return self.model_copy(update={"checksum": self.computed_checksum()})

    def verify_checksum(self) -> None:
        if not self.checksum or self.checksum != self.computed_checksum():
            raise ValueError("signal bundle checksum is missing or invalid")


def stable_id(*parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
