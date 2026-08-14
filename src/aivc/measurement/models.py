from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MeasurementModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncHealth(MeasurementModel):
    status: str | None = None
    last_synced_at: datetime | None = None
    heartbeat_at: datetime | None = None
    error: str | None = None


class SourceHealth(MeasurementModel):
    source: Literal["gsc", "ga4"]
    connected: bool
    handle_fingerprint: str | None = None
    clients_sharing_handle: int = 0
    duplicate_natural_keys: int = 0
    row_count: int = 0
    earliest_date: date | None = None
    latest_date: date | None = None
    lag_days: int | None = None
    fresh: bool = False
    rows_with_conversions: int | None = None
    rows_with_revenue: int | None = None
    sync: SyncHealth = Field(default_factory=SyncHealth)


class IsolationHealth(MeasurementModel):
    ready: bool
    source_tables_server_only: bool
    derived_tables_tenant_scoped: bool
    derived_tables_backend_write_only: bool
    rls_enabled_tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MeasurementFoundationHealth(MeasurementModel):
    mode: Literal["read_only"] = "read_only"
    client_id: UUID
    company_name: str
    currency_code: str | None = None
    gsc: SourceHealth
    ga4: SourceHealth
    isolation: IsolationHealth
    ready_for_gsc_measurement: bool
    ready_for_ga4_measurement: bool
    ready_for_recorded_revenue: bool
    tenant_scope: Literal["server_side_client_mapping"] = "server_side_client_mapping"
    warnings: list[str] = Field(default_factory=list)


class SubjectType(StrEnum):
    recon_recommendation = "recon_recommendation"
    citation_signal = "citation_signal"


class VerificationStatus(StrEnum):
    snapshot_confirmed = "snapshot_confirmed"
    manual_confirmed = "manual_confirmed"


class WindowType(StrEnum):
    baseline = "baseline"
    follow_up = "follow_up"


class SourceName(StrEnum):
    gsc = "gsc"
    ga4 = "ga4"


class MeasurementWindows(MeasurementModel):
    baseline_start: date
    baseline_end: date
    follow_up_start: date
    follow_up_end: date


class StartMeasurementRequest(MeasurementModel):
    client_id: UUID
    subject_type: SubjectType
    subject_id: str = Field(min_length=1, max_length=500)
    implemented_at: datetime
    implemented_by: str = Field(min_length=1, max_length=500)
    verification_status: VerificationStatus
    action_type: str = Field(default="other", min_length=1, max_length=100)
    target_pages: list[str] = Field(default_factory=list)
    target_queries: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    implementation_notes: str | None = Field(default=None, max_length=10_000)
    baseline_days: int = Field(default=28, ge=7, le=180)
    stabilization_days: int = Field(default=7, ge=0, le=90)
    follow_up_days: int = Field(default=28, ge=7, le=180)
    require_ga4: bool = False

    @model_validator(mode="after")
    def validate_measurement_targets(self) -> StartMeasurementRequest:
        if self.implemented_at.tzinfo is None or self.implemented_at.utcoffset() is None:
            raise ValueError("implemented_at must include a timezone offset")
        if not any(value.strip() for value in [*self.target_pages, *self.target_queries]):
            raise ValueError("At least one target page or target query is required")
        if self.require_ga4 and not any(value.strip() for value in self.target_pages):
            raise ValueError("GA4 measurement requires at least one exact target page")
        return self


class StartMeasurementResult(MeasurementModel):
    action_execution_id: UUID
    plan_id: UUID
    plan_status: str
    windows: MeasurementWindows
    required_sources: list[SourceName]


class SourceSnapshot(MeasurementModel):
    source: SourceName
    window_type: WindowType
    requested_start: date
    requested_end: date
    effective_start: date | None = None
    effective_end: date | None = None
    fresh_through: date | None = None
    row_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    source_status: Literal["available", "not_connected", "stale", "unmapped", "refused", "empty"]
    warnings: list[str] = Field(default_factory=list)
    payload_checksum: str


class MeasurementOutcome(MeasurementModel):
    plan_id: UUID
    classification: Literal[
        "observed_increase",
        "observed_decrease",
        "mixed",
        "no_observed_change",
        "insufficient_evidence",
    ]
    confidence: Literal["high", "medium", "low", "none"]
    gsc_deltas: list[dict[str, Any]] = Field(default_factory=list)
    ga4_deltas: list[dict[str, Any]] = Field(default_factory=list)
    revenue_category: Literal["recorded", "unavailable"] = "unavailable"
    recorded_revenue: float | None = None
    observed_revenue_delta: float | None = None
    currency_code: str | None = None
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
