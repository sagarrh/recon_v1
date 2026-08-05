"""Turn stored measurements into the compact outcome card a client actually reads.

The card reports a funnel, one layer at a time:

    AI visibility  ->  search demand  ->  website engagement  ->  commercial intent  ->  revenue

Two rules govern it, and they are the whole point:

**A layer with no data says `unavailable`, never zero.** "No GA4 connection" and "no conversions"
are completely different facts, and collapsing them would let a missing integration read as a
failed intervention.

**Reporting only the layers you can evidence is still a good result.** A card showing visibility and
search improving, with conversions and revenue marked unavailable, is honest and useful. Filling
those gaps with a modeled number would make it neither.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect

# Funnel layers, in the order a client experiences them.
AI_VISIBILITY = "ai_visibility"
SEARCH_DEMAND = "search_demand"
WEBSITE_ENGAGEMENT = "website_engagement"
COMMERCIAL_INTENT = "commercial_intent"
REVENUE = "revenue"

LAYER_ORDER = (AI_VISIBILITY, SEARCH_DEMAND, WEBSITE_ENGAGEMENT, COMMERCIAL_INTENT, REVENUE)
LAYER_LABEL = {
    AI_VISIBILITY: "AI visibility",
    SEARCH_DEMAND: "Google Search",
    WEBSITE_ENGAGEMENT: "Website",
    COMMERCIAL_INTENT: "Commercial intent",
    REVENUE: "Revenue",
}

IMPROVED = "improved"
DECLINED = "declined"
FLAT = "flat"
MIXED = "mixed"          # the layer's own metrics disagree with each other
UNAVAILABLE = "unavailable"

# Layers with no source wired up yet. Named explicitly so the card says WHY, rather than leaving a
# reader to guess whether the number was zero or simply never collected.
NOT_YET_INSTRUMENTED = {
    WEBSITE_ENGAGEMENT: "GA4 engagement is not connected for this client yet",
    COMMERCIAL_INTENT: "GA4 key events are not connected for this client yet",
    REVENUE: "no ecommerce revenue source connected; CRM is out of scope",
}


@dataclass(frozen=True)
class FunnelLayer:
    name: str
    status: str
    detail: str
    metrics: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LAYER_LABEL.get(self.name, self.name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.name, "label": self.label, "status": self.status,
            "detail": self.detail, "metrics": self.metrics,
        }


@dataclass(frozen=True)
class OutcomeCard:
    recommendation_id: str
    action: str
    implemented_at: datetime | None
    implemented_by: str | None
    layers: list[FunnelLayer]
    assessment: str
    confidence: str
    why_confidence_is_not_higher: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "action": self.action,
            "implemented_at": self.implemented_at,
            "implemented_by": self.implemented_by,
            "layers": [layer.as_dict() for layer in self.layers],
            "assessment": self.assessment,
            "confidence": self.confidence,
            "why_confidence_is_not_higher": self.why_confidence_is_not_higher,
        }


def _direction(delta: float, *, flat_below: float) -> str:
    """Grade a signed movement, treating anything under `flat_below` as no real change."""
    if abs(delta) < flat_below:
        return FLAT
    return IMPROVED if delta > 0 else DECLINED


def _combine(improved: int, declined: int) -> str:
    """Roll several metric directions into one layer status."""
    if improved and declined:
        return MIXED
    if improved:
        return IMPROVED
    if declined:
        return DECLINED
    return FLAT


def _visibility_layer(row: dict[str, Any]) -> FunnelLayer:
    """AI answer visibility, from the Recon outcome measured against the frozen SOV baseline."""
    baseline = row.get("baseline_client_sov_pp")
    current = row.get("current_client_sov_pp")
    delta = row.get("sov_delta_pp")
    if delta is None or baseline is None or current is None:
        return FunnelLayer(
            AI_VISIBILITY, UNAVAILABLE,
            "share of voice was not measured for this recommendation",
        )
    status = _direction(float(delta), flat_below=0.5)
    return FunnelLayer(
        AI_VISIBILITY, status,
        f"{float(baseline):.1f}% → {float(current):.1f}% ({float(delta):+.1f}pp)",
        [{"metric": "client_share_of_voice", "baseline": float(baseline),
          "follow_up": float(current), "absolute_delta": float(delta),
          "unit": "percentage_points"}],
    )


def _search_layer(row: dict[str, Any]) -> FunnelLayer:
    """Google Search performance, from the GSC measurement outcome."""
    classification = row.get("classification")
    if not classification:
        return FunnelLayer(
            SEARCH_DEMAND, UNAVAILABLE,
            "no Search Console measurement has been evaluated for this recommendation",
        )
    deltas = list(row.get("gsc_deltas") or [])
    if classification in ("source_not_connected", "unmapped", "waiting_for_window",
                          "insufficient_data"):
        return FunnelLayer(SEARCH_DEMAND, UNAVAILABLE, _explain(classification), deltas)

    directions = {d.get("metric"): d.get("direction") for d in deltas}
    status = _combine(
        sum(1 for v in directions.values() if v == IMPROVED),
        sum(1 for v in directions.values() if v == DECLINED),
    )

    parts = []
    for delta in deltas:
        if delta.get("absolute_delta") is None:
            continue
        relative = delta.get("relative_delta")
        if relative is not None:
            parts.append(f"{delta['metric']}: {relative * 100:+.0f}%")
        else:
            parts.append(f"{delta['metric']}: {delta['absolute_delta']:+.1f}")
    return FunnelLayer(SEARCH_DEMAND, status, "; ".join(parts) or classification, deltas)


def _explain(classification: str) -> str:
    return {
        "source_not_connected": "Search Console is not connected, or its property is shared "
                                "with another client and was not attributed",
        "unmapped": "no target query or page could be matched in Search Console",
        "waiting_for_window": "the follow-up window has not closed yet",
        "insufficient_data": "too little search volume in the baseline for a percentage to mean "
                             "anything",
    }.get(classification, classification)


def build_card(row: dict[str, Any]) -> OutcomeCard:
    """Build one card from a joined execution + recon outcome + measurement outcome row."""
    layers = [_visibility_layer(row), _search_layer(row)]
    layers.extend(
        FunnelLayer(name, UNAVAILABLE, reason)
        for name, reason in ((n, NOT_YET_INSTRUMENTED[n]) for n in LAYER_ORDER[2:])
    )

    evidenced = [layer for layer in layers if layer.status != UNAVAILABLE]
    improved = [layer for layer in evidenced if layer.status == IMPROVED]
    declined = [layer for layer in evidenced if layer.status == DECLINED]
    # A layer whose own metrics disagree is itself mixed evidence. Without this it is neither
    # improved nor declined and falls through to "no material change" — which understates a real
    # movement and, worse, reports it with medium confidence.
    mixed = [layer for layer in evidenced if layer.status == MIXED]

    if not evidenced:
        assessment, confidence = "no measurable evidence yet", "none"
    elif mixed or (improved and declined):
        assessment, confidence = "mixed result", "low"
    elif improved:
        assessment = "associated downstream growth"
        # Two layers moving together is a stronger signal than one, but never "high": without a
        # control there is no basis for that, whatever the numbers look like.
        confidence = "medium" if len(improved) >= 2 else "low"
    elif declined:
        assessment, confidence = "associated decline", "medium" if len(declined) >= 2 else "low"
    else:
        assessment, confidence = "no material change", "medium"

    reasons = list(row.get("limitations") or [])
    reasons.extend(
        f"{LAYER_LABEL[layer.name]} unavailable — {layer.detail}"
        for layer in layers if layer.status == UNAVAILABLE
    )

    return OutcomeCard(
        recommendation_id=str(row.get("recommendation_id") or row.get("subject_id") or ""),
        action=str(row.get("cluster_label") or "recommendation"),
        implemented_at=row.get("implemented_at"),
        implemented_by=row.get("implemented_by"),
        layers=layers,
        assessment=assessment,
        confidence=confidence,
        why_confidence_is_not_higher=reasons,
    )


_CARD_SQL = """
    select execution.subject_id                as recommendation_id,
           execution.implemented_at,
           execution.implemented_by,
           rec.cluster_label,
           outcome.baseline_client_sov_pp,
           outcome.current_client_sov_pp,
           outcome.sov_delta_pp,
           measured.classification,
           measured.confidence                 as measurement_confidence,
           measured.gsc_deltas,
           measured.limitations
      from public.aivc_action_executions execution
      left join public.recommendations rec
             on rec.id::text = execution.subject_id
      left join public.scout_outcomes outcome
             on outcome.recommendation_id::text = execution.subject_id
      left join public.ai_visibility_measurement_plans plan
             on plan.subject_id = execution.subject_id
      left join public.ai_visibility_measurement_outcomes measured
             on measured.plan_id = plan.id
     where execution.client_id = %s
       and execution.status in ('executed', 'verified')
     order by execution.implemented_at desc
"""


def load_outcome_cards(settings: Settings, *, client_id: UUID) -> list[OutcomeCard]:
    """Every executed recommendation for a client, as a funnel card."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(_CARD_SQL, (client_id,)).fetchall()
    return [build_card(dict(row)) for row in rows]
