"""Turn two window snapshots into one conservative, auditable conclusion.

The classification is deliberately timid. It reports what moved and refuses to say why: a
before/after comparison with no control cannot separate the action from a seasonal swing, a
competitor's outage, or an algorithm update that week.

So the vocabulary is associative — "followed by", "associated with" — never causal, and every
outcome carries its own limitations. A result presented without its limits reads as more certain
than it is, and that gap is where a measurement system loses a client's trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Terminal states, in rough order of how much they let you say.
WAITING_FOR_WINDOW = "waiting_for_window"
SOURCE_NOT_CONNECTED = "source_not_connected"
UNMAPPED = "unmapped"
INSUFFICIENT_DATA = "insufficient_data"
SEARCH_GROWTH = "associated_search_growth"
NO_MATERIAL_CHANGE = "no_material_change"
SEARCH_DECLINE = "associated_search_decline"
MIXED = "mixed_result"

# What counts as a real move rather than noise. Deliberately explicit and versioned: an
# unstated threshold is an unfalsifiable claim.
MATERIAL_RELATIVE_DELTA = 0.10   # 10% change in impressions or clicks
MATERIAL_POSITION_DELTA = 1.0    # one whole ranking place, impression-weighted
MIN_BASELINE_IMPRESSIONS = 50    # below this, percentages are noise

ALGORITHM_VERSION = "outcome_v1"

# Always-present limitations of a single before/after window with no control.
BASE_LIMITATIONS = (
    "before/after comparison with no control page or topic group",
    "a single measurement window; seasonality is not separated",
    "association only — this does not establish that the action caused the change",
)


@dataclass(frozen=True)
class Outcome:
    classification: str
    confidence: str
    deltas: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    algorithm_version: str = ALGORITHM_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "confidence": self.confidence,
            "deltas": self.deltas,
            "limitations": self.limitations,
            "warnings": self.warnings,
            "evidence": self.evidence,
            "algorithm_version": self.algorithm_version,
        }


def _material(delta: dict[str, Any]) -> str | None:
    """Direction of a material move, or None when the move is within noise."""
    metric = delta.get("metric")
    absolute = delta.get("absolute_delta")
    relative = delta.get("relative_delta")
    if absolute is None:
        return None

    if metric == "average_position":
        if abs(absolute) < MATERIAL_POSITION_DELTA:
            return None
        return "improved" if absolute < 0 else "declined"   # smaller position is better

    if metric in ("impressions", "clicks"):
        if relative is None:
            # Growth from a zero baseline is real but unquantifiable as a ratio; treat any
            # non-trivial absolute move as material rather than discarding it.
            return "improved" if absolute > 0 else None
        if abs(relative) < MATERIAL_RELATIVE_DELTA:
            return None
        return "improved" if absolute > 0 else "declined"

    return None   # CTR moves as a consequence of the other two; not judged independently


def classify_gsc(
    deltas: list[dict[str, Any]],
    *,
    baseline_impressions: int,
    window_status: str,
    source_status: str,
    warnings: list[str] | None = None,
) -> Outcome:
    """Draw the most conservative conclusion the evidence supports."""
    warnings = list(warnings or [])
    limitations = list(BASE_LIMITATIONS)

    if window_status == "waiting_for_window":
        return Outcome(WAITING_FOR_WINDOW, "none", deltas, limitations, warnings,
                       {"reason": "follow-up window has not closed yet"})
    if window_status == "no_anchor":
        return Outcome(UNMAPPED, "none", deltas, limitations, warnings,
                       {"reason": "no confirmed implementation date to measure from"})
    if source_status in ("not_connected", "no_handle", "unknown_client"):
        return Outcome(SOURCE_NOT_CONNECTED, "none", deltas, limitations, warnings,
                       {"reason": "Search Console is not connected for this client"})
    if source_status in ("shared_handle", "refused", "excluded"):
        return Outcome(SOURCE_NOT_CONNECTED, "none", deltas, limitations, warnings,
                       {"reason": f"source refused: {source_status}"})
    if source_status == "unmapped":
        return Outcome(UNMAPPED, "none", deltas, limitations, warnings,
                       {"reason": "no target query or page matched this source"})

    if baseline_impressions < MIN_BASELINE_IMPRESSIONS:
        limitations.append(
            f"baseline had {baseline_impressions} impressions, below the "
            f"{MIN_BASELINE_IMPRESSIONS} needed for percentages to be meaningful"
        )
        return Outcome(INSUFFICIENT_DATA, "none", deltas, limitations, warnings,
                       {"baseline_impressions": baseline_impressions})

    directions = [d for d in (_material(delta) for delta in deltas) if d]
    improved = directions.count("improved")
    declined = directions.count("declined")

    if not directions:
        classification, confidence = NO_MATERIAL_CHANGE, "medium"
    elif improved and declined:
        classification, confidence = MIXED, "low"
    elif improved:
        classification = SEARCH_GROWTH
        # Two or more independent metrics moving together is a stronger signal than one.
        confidence = "medium" if improved >= 2 else "low"
    else:
        classification, confidence = SEARCH_DECLINE, "medium" if declined >= 2 else "low"

    if warnings:
        limitations.append("source warnings were raised for this measurement; see warnings")

    return Outcome(
        classification=classification,
        confidence=confidence,
        deltas=deltas,
        limitations=limitations,
        warnings=warnings,
        evidence={
            "material_improvements": improved,
            "material_declines": declined,
            "baseline_impressions": baseline_impressions,
            "thresholds": {
                "relative_delta": MATERIAL_RELATIVE_DELTA,
                "position_delta": MATERIAL_POSITION_DELTA,
                "min_baseline_impressions": MIN_BASELINE_IMPRESSIONS,
            },
        },
    )
