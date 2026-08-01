# sov.py — Pydantic models for Share-of-Voice tracking and investigation triggers.
# Purpose: Defines SOVTrackingRecord, InvestigationTrigger, and CycleSummary — core detection-layer artifacts.
# Scope: Structured outputs only; alert_type / shift_type / priority enforce enum patterns.
# Consumers: scout/nodes/sov_detection.py + blog_monitoring.py produce them; graph merge/writer/reader round-trip them.
from datetime import date

from pydantic import BaseModel, Field


class SOVTrackingRecord(BaseModel):
    model_config = {"extra": "ignore"}
    client_id: str
    cluster_id: str
    competitor_name: str
    week_date: date
    sov_score: float
    rolling_avg_4w: float | None = None
    rolling_std_4w: float | None = None
    change_vs_avg: float | None = None
    z_score: float | None = None
    client_sov_this_week: float | None = None
    client_sov_change_vs_avg: float | None = None
    alert_triggered: bool = False
    alert_type: str | None = Field(None, pattern="^(gain|loss|displacement|new_entrant)$")
    alert_reason: str | None = None


class InvestigationTrigger(BaseModel):
    model_config = {"extra": "ignore"}
    client_id: str
    client_name: str
    competitor_name: str
    competitor_domain: str
    cluster_id: str
    cluster_label: str
    shift_type: str = Field(..., pattern="^(gain|loss|displacement|new_entrant|blog_detected)$")
    shift_magnitude: float
    shift_in_sd_units: float | None = None
    client_sov_change: float | None = None
    correlated_displacement: bool = False
    investigation_priority: str = Field(..., pattern="^(urgent|standard|opportunistic)$")
    triage_reason: str
    blog_post_url: str | None = None
    blog_post_title: str | None = None
    detection_source: str | None = None
    blog_evidence: list[dict] = []   # evidence from blog triggers that collided with this trigger's key (kept, not dropped)
    triage_severity: str = ""   # TFS-08: CRITICAL|ELEVATED|WATCH|WIN|NOISE; "" until classified
    graduation_regime: str = "news_mode"   # R4-3: which detection regime produced this trigger — news_mode|graduated


class ClusterVerdict(BaseModel):
    model_config = {"extra": "ignore"}
    client_id: str
    client_name: str = ""
    cluster_id: str
    cluster_label: str = ""
    primary_competitor: str = ""
    primary_competitor_domain: str = ""
    field: list[dict] = []                 # [{competitor, competitor_domain, delta_pp}] across the whole field
    shift_type: str = ""                   # primary trigger's shift_type
    delta_client: float | None = None   # client SOV change vs 4-week avg (pp)
    correlated_displacement: bool = False
    triage_severity: str = ""              # set by TFS-08 (CRITICAL|ELEVATED|WATCH|WIN|NOISE); "" until classified
    investigation_priority: str = "standard"
    noise: bool = False                    # set by TFS-09 noise floor
    graduation_regime: str = "news_mode"   # R4-3: regime of the primary trigger — news_mode|graduated
    # Tier 1 — revenue stamped in memory by recommendation_gen (not by field_resolution).
    revenue_at_risk_usd: float | None = Field(None, ge=0)
    revenue_basis: str = Field("none", pattern="^(actual|modeled|hybrid|none)$")


class CycleSummary(BaseModel):
    model_config = {"extra": "ignore"}
    run_id: str
    sync_date: date
    clients_processed: int
    clusters_processed: int
    competitor_cluster_pairs_evaluated: int
    investigations_triggered: int
    investigations_by_type: dict[str, int]
    no_significant_shifts_clients: list[str] = []
    capped_investigations: list[str] = []
    data_quality_errors: int = 0
    total_tokens_used: int = 0
