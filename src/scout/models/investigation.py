# investigation.py — Pydantic models for the four parallel investigation sources.
# Purpose: Defines WebsiteChanges, ThirdPartySignals, AICitationChange, and ClientReadiness — the structured outputs from the investigation nodes.
# Scope: Structured outputs only; field patterns enforce enum-like values (confidence, etc.).
# Consumers: scout/nodes/website_diff.py, scout/nodes/web_intelligence.py, scout/nodes/ai_response_analysis.py, scout/nodes/client_readiness.py, state.py.

from pydantic import BaseModel, Field


class WebsiteChanges(BaseModel):
    model_config = {"extra": "ignore"}
    competitor_name: str
    cluster_id: str
    new_pages: list[dict]
    modified_pages: list[dict]
    schema_added: list[str]
    content_themes: list[str]
    cluster_relevance: str
    summary: str = Field(..., min_length=20)
    confidence: str = Field(..., pattern="^(high|medium|low|none)$")
    abstained: bool = False              # R3-3: True when this source looked but found nothing above its evidence floor
    abstention_counts: dict = {}         # R3-3: the raw counts that tripped the floor (e.g. {"changed_elements": 0})
    ai_access: dict = {}                 # competitor's live AI-access files {llms_txt, robots_txt, ai_bots} — compared vs ClientReadiness in reports


class ThirdPartySignals(BaseModel):
    model_config = {"extra": "ignore"}
    competitor_name: str
    cluster_id: str
    queries_run: list[dict]
    review_activity: dict
    press_and_publications: dict
    directory_and_listings: dict
    linkedin_and_social: dict
    awards_and_certifications: dict
    inferred_trigger: str | None = None
    confidence: str = Field(..., pattern="^(high|medium|low|none)$")
    summary: str = Field(..., min_length=20)
    abstained: bool = False              # R3-3: True when this source looked but found nothing above its evidence floor
    abstention_counts: dict = {}         # R3-3: the raw counts that tripped the floor (e.g. {"usable_serp_rows": 0})


class AICitationChange(BaseModel):
    model_config = {"extra": "ignore"}
    competitor_name: str
    cluster_id: str
    previous_positioning: str
    current_positioning: str
    citation_frequency_change: dict
    positioning_shifts: list[dict]
    new_claims: list[str]
    dropped_claims: list[str]
    inferred_trigger: str | None = None
    delta_summary: str = Field(..., min_length=20)
    abstained: bool = False              # R3-3: True when this source looked but found nothing above its evidence floor
    abstention_counts: dict = {}         # R3-3: the raw counts that tripped the floor (e.g. {"paired_weeks": 0})


class CitationInvestigationEvidence(BaseModel):
    """Deterministic citation evidence supplied by the canonical AIVC producer."""

    model_config = {"extra": "forbid"}
    client_id: str
    competitor_name: str
    cluster_id: str
    matched_signal_ids: list[str] = Field(default_factory=list)
    provider_query_changes: list[dict] = Field(default_factory=list)
    visibility_changes: list[dict] = Field(default_factory=list)
    citation_source_changes: list[dict] = Field(default_factory=list)
    page_evidence_refs: list[str] = Field(default_factory=list)
    confidence: str = Field(default="none", pattern="^(high|medium|low|none)$")
    warnings: list[str] = Field(default_factory=list)
    abstained: bool = False
    abstention_counts: dict[str, int] = Field(default_factory=dict)


class ClientReadiness(BaseModel):
    model_config = {"extra": "ignore"}
    client_id: str
    llms_txt_present: bool = False
    ai_bots_present: bool = False
    robots_present: bool = False
    schema_types_present: list[str] = []
    schema_types_missing: list[str] = []
    readiness_summary: str = Field(..., min_length=20)
    confidence: str = Field(..., pattern="^(high|medium|low|none)$")


class ClientGapRecommendation(BaseModel):
    """A standing client-side GEO action derived from a DETECTED gap — a missing recon fact, a site-readiness gap,
    or a competitor asset the client lacks — not tied to a competitor SOV shift. Surfaced in the recon profile, the
    client export, and the client summary/report."""
    model_config = {"extra": "ignore"}
    code: str                              # stable id, e.g. "same_as_missing" / "no_reviews"
    area: str = Field(..., pattern="^(entity_authority|reviews|structured_data|ai_access|content_demand|competitive)$")
    title: str
    gap_signal: str                        # which field/signal detected the gap
    why_ai_visibility: str                 # why it matters for AI answer-engine citation
    client_action: str                     # the concrete action the client takes
    priority: str = Field(..., pattern="^(high|medium|low)$")
    source: str = Field(..., pattern="^(recon|readiness|competitive)$")
    detail: dict = {}                      # optional extras (e.g. missing_platforms, competitor)
