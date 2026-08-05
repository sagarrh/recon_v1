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
