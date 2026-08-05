# recommendation.py — Pydantic models for recommendation and report artifacts.
# Purpose: Defines Recommendation, InternalReport, and ClientSummary — the terminal artifacts of the pipeline.
# Scope: Structured outputs only; field constraints enforce priority/type enums, action-bullet cardinality, and length floors.
# Consumers: scout/nodes/recommendation_gen.py, scout/nodes/report_gen.py, scout/nodes/slack_delivery.py, writer.py.
from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    model_config = {"extra": "ignore"}
    investigation_id: str
    client_id: str
    client_name: str = ""
    competitor_name: str
    cluster_id: str
    cluster_label: str
    shift_type: str = Field(..., pattern="^(gain|loss|displacement|new_entrant|blog_detected)$")
    type: str = Field(..., pattern="^(offensive|defensive)$")
    priority: str = Field(..., pattern="^(urgent|standard|opportunistic)$")
    probable_cause: str = Field(..., min_length=10)
    confidence: str = Field(..., pattern="^(high|medium|low|unknown)$")
    gap_analysis: str = Field(..., min_length=20)
    action_bullets: list[str] = Field(..., min_length=3, max_length=5)
    summary: str
    slack_report: str
    timeline: str = ""
    window_weeks: int = 0      # measurement-window upper bound (weeks); outcome_measure prefers this over timeline prose
    # What this recommendation targets, validated in scout/targets.py. These are what make an outcome
    # measurable at all: GSC is measured at target_queries, GA4 at target_pages. Pages are always
    # client-owned — a competitor or third-party URL is rejected, never carried.
    target_pages: list[str] = Field(default_factory=list)
    target_queries: list[str] = Field(default_factory=list)
    action_type: str = Field(
        "other",
        pattern="^(content_update|new_page|schema|ai_access|third_party|measurement|other)$",
    )
    mapping_confidence: str = Field("unmapped", pattern="^(exact|query_only|unmapped)$")
    target_rejections: list[dict] = Field(default_factory=list)
    expected_leading_outcome: str = ""    # e.g. "more AI citations and GSC impressions"
    expected_business_outcome: str = ""   # e.g. "more qualified visits and demo requests"
    # Commercial qualification (scout/priority.py). Transparent and additive — never a dollar figure.
    # priority_components carries the full vector so any score can be explained, not just trusted.
    priority_score: float = Field(0.0, ge=0.0, le=100.0)
    priority_band: str = Field("low", pattern="^(critical|high|medium|low)$")
    priority_components: dict = Field(default_factory=dict)
    # Carried GEO context (in-memory only; NOT persisted by sed_writer) — surfaced deterministically in report_gen.
    revenue_context: dict = Field(default_factory=dict)
    co_mention_density: dict = Field(default_factory=dict)
    client_readiness: dict = Field(default_factory=dict)
    client_gaps: list[dict] = Field(default_factory=list)  # standing client-side GEO gap recommendations
    competitor_ai_access: dict = Field(default_factory=dict)
    # Revenue evidence (deterministic; persisted by sed_writer). `revenue_value_usd` is meaningless
    # without `revenue_category` — never render or persist one without the other, and never add values
    # across categories. `unavailable` is the honest default, not a failure state.
    revenue_category: str = Field(
        "unavailable",
        pattern="^(recorded|influenced|incremental_estimate|modeled_scenario|unavailable)$",
    )
    revenue_value_usd: float | None = Field(None, ge=0)
    revenue_currency: str = "USD"
    revenue_limitations: list[str] = Field(default_factory=list)
    revenue_inputs: dict = Field(default_factory=dict)
    # Pre-publish validation gate (TFS-05): 'ok' | 'quarantined'; notes carry the reasons.
    validation_status: str = "ok"
    validation_notes: list[str] = Field(default_factory=list)


class InternalReport(BaseModel):
    model_config = {"extra": "ignore"}
    client_id: str
    client_name: str = ""
    competitor_name: str
    cluster_id: str
    cluster_label: str
    priority: str
    report_text: str = Field(..., min_length=200)
    validation_status: str = "ok"
    validation_notes: list[str] = Field(default_factory=list)


class ClientSummary(BaseModel):
    model_config = {"extra": "ignore"}
    client_id: str
    client_name: str = ""
    competitor_name: str = ""
    cluster_id: str
    cluster_label: str
    summary_text: str = Field(..., max_length=3500)
    validation_status: str = "ok"
    validation_notes: list[str] = Field(default_factory=list)
