# priority.py — commercially qualify a signal with a transparent score (pure, no I/O, no dollars).
# Purpose: decide which visibility movements deserve an action, without inventing a revenue figure.
# Scope: pure functions over already-gathered evidence. No DB, no network, no LLM.
# Consumers: scout/nodes/recommendation_gen.py.
#
# Why this exists: the previous ordering signal was a SOV-derived dollar amount, which ranked
# clusters by how SMALL the client's share was rather than how commercially important they were.
# This replaces it with an auditable vector — every component is published alongside the total, so a
# score can always be explained rather than trusted.
#
# The load-bearing rule: a MISSING input is not a zero. Scoring an unknown as zero silently
# penalises clients with less instrumentation, which would push effort away from exactly the
# accounts we understand least. Missing components are excluded and the remaining weights are
# renormalised, with what was missing recorded on the result.
from dataclasses import dataclass, field

PRIORITY_SCORE_VERSION = "priority_v1"

# Relative importance of each component. Operator-tunable via ScoutConfig.priority_weights.
DEFAULT_WEIGHTS: dict[str, float] = {
    "competitor_advantage": 1.0,    # how much ground a competitor just took
    "client_visibility_gap": 1.0,   # how far the client is from where it could be
    "search_demand": 1.2,           # real Google demand behind the topic
    "actionability": 1.0,           # is there a page we can actually change
    "evidence_confidence": 0.8,     # how much we trust the diagnosis
    "implementation_effort": 0.6,   # inverse: cheap actions rank higher
    "client_readiness": 0.6,        # can this client's site convert the visibility
}

# Score -> band. Bands are what operators act on; the raw score is for ordering and audit.
BAND_THRESHOLDS = ((70.0, "critical"), (50.0, "high"), (30.0, "medium"))
DEFAULT_BAND = "low"

# Rough effort ranking per action type; cheaper actions score higher on `implementation_effort`.
_EFFORT_BY_ACTION = {
    "ai_access": 0.95,        # a file at a known path
    "schema": 0.85,           # structured data on an existing page
    "measurement": 0.8,
    "content_update": 0.6,    # rewrite an existing page
    "new_page": 0.3,          # net-new content
    "third_party": 0.2,       # depends on someone else saying yes
    "other": 0.5,
}

_CONFIDENCE_VALUE = {"high": 1.0, "medium": 0.6, "low": 0.3, "unknown": None}

# Impressions at which search demand is considered fully material. Above this the component
# saturates — the difference between 50k and 500k monthly impressions does not change what to do.
DEMAND_SATURATION_IMPRESSIONS = 5000.0
# Percentage-point move treated as a full-strength competitive signal.
ADVANTAGE_SATURATION_PP = 15.0


@dataclass(frozen=True)
class Component:
    name: str
    value: float | None      # 0..1, or None when the input was unavailable
    weight: float
    reason: str

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class PriorityScore:
    score: float                     # 0..100 over the components that had inputs
    band: str
    version: str
    components: list[Component] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    coverage: float = 0.0            # share of total weight that had inputs

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "band": self.band,
            "version": self.version,
            "coverage": round(self.coverage, 3),
            "missing_inputs": list(self.missing_inputs),
            "components": [
                {
                    "name": c.name,
                    "value": None if c.value is None else round(c.value, 3),
                    "weight": c.weight,
                    "reason": c.reason,
                }
                for c in self.components
            ],
        }


def _saturating(value: float | None, ceiling: float) -> float | None:
    """Map a magnitude onto 0..1, saturating at `ceiling`. None stays None."""
    if value is None:
        return None
    return max(0.0, min(1.0, abs(float(value)) / ceiling))


def _competitor_advantage(shift_magnitude) -> Component:
    value = _saturating(shift_magnitude, ADVANTAGE_SATURATION_PP)
    return Component(
        "competitor_advantage", value, DEFAULT_WEIGHTS["competitor_advantage"],
        "no shift magnitude on the trigger" if value is None
        else f"competitor moved {abs(float(shift_magnitude)):.1f}pp",
    )


def _client_visibility_gap(client_sov_pp) -> Component:
    """Lower client share means more room to gain. Not a dollar — just distance from the ceiling."""
    if client_sov_pp is None:
        return Component("client_visibility_gap", None,
                         DEFAULT_WEIGHTS["client_visibility_gap"], "client SOV unavailable")
    share = max(0.0, min(1.0, float(client_sov_pp) / 100.0))
    return Component("client_visibility_gap", 1.0 - share,
                     DEFAULT_WEIGHTS["client_visibility_gap"],
                     f"client holds {float(client_sov_pp):.1f}pp of this cluster")


def _search_demand(impressions) -> Component:
    """Real Google demand behind the topic. Absent when GSC is not connected — never assumed zero."""
    value = _saturating(impressions, DEMAND_SATURATION_IMPRESSIONS)
    return Component(
        "search_demand", value, DEFAULT_WEIGHTS["search_demand"],
        "no GSC demand for this cluster" if value is None
        else f"{int(impressions):,} impressions in the demand window",
    )


def _actionability(mapping_confidence: str) -> Component:
    """Whether there is something concrete to change. An unmapped signal cannot be acted on or
    measured, so it ranks below one with a real target page."""
    value = {"exact": 1.0, "query_only": 0.5, "unmapped": 0.0}.get(mapping_confidence)
    return Component(
        "actionability", value, DEFAULT_WEIGHTS["actionability"],
        f"mapping_confidence={mapping_confidence}" if value is not None
        else "mapping confidence unknown",
    )


def _evidence_confidence(confidence: str) -> Component:
    value = _CONFIDENCE_VALUE.get((confidence or "").lower())
    return Component(
        "evidence_confidence", value, DEFAULT_WEIGHTS["evidence_confidence"],
        f"diagnosis confidence={confidence}" if value is not None
        else "cause not established",
    )


def _implementation_effort(action_type: str) -> Component:
    value = _EFFORT_BY_ACTION.get(action_type)
    return Component(
        "implementation_effort", value, DEFAULT_WEIGHTS["implementation_effort"],
        f"action_type={action_type}" if value is not None else "action type unknown",
    )


def _client_readiness(readiness) -> Component:
    """Can this client's site actually convert new visibility? Derived from the readiness node's
    observations, which are absent when that node did not run."""
    if not readiness:
        return Component("client_readiness", None, DEFAULT_WEIGHTS["client_readiness"],
                         "client readiness not assessed")
    get = readiness.get if isinstance(readiness, dict) else lambda k, d=None: getattr(readiness, k, d)
    if (get("confidence", "none") or "none") == "none":
        return Component("client_readiness", None, DEFAULT_WEIGHTS["client_readiness"],
                         "client readiness not assessed")
    signals = [
        bool(get("llms_txt_present")),
        bool(get("robots_present")),
        not (get("schema_types_missing") or []),
    ]
    value = sum(1 for s in signals if s) / len(signals)
    return Component("client_readiness", value, DEFAULT_WEIGHTS["client_readiness"],
                     f"{sum(signals)}/{len(signals)} readiness signals present")


def band_for(score: float) -> str:
    for threshold, name in BAND_THRESHOLDS:
        if score >= threshold:
            return name
    return DEFAULT_BAND


def score_signal(
    *,
    shift_magnitude=None,
    client_sov_pp=None,
    search_impressions=None,
    mapping_confidence: str = "unmapped",
    evidence_confidence: str = "unknown",
    action_type: str = "other",
    client_readiness=None,
    weights: dict[str, float] | None = None,
) -> PriorityScore:
    """Score how much a visibility movement deserves action. Transparent, additive, no dollars.

    Components with no input are excluded and the remaining weights renormalised, so a client with
    GSC disconnected is not ranked below an identical client that has it — they are simply scored on
    less evidence, and `coverage` says how much."""
    components = [
        _competitor_advantage(shift_magnitude),
        _client_visibility_gap(client_sov_pp),
        _search_demand(search_impressions),
        _actionability(mapping_confidence),
        _evidence_confidence(evidence_confidence),
        _implementation_effort(action_type),
        _client_readiness(client_readiness),
    ]
    if weights:
        components = [
            Component(c.name, c.value, float(weights.get(c.name, c.weight)), c.reason)
            for c in components
        ]

    available = [c for c in components if c.available]
    total_weight = sum(c.weight for c in components)
    used_weight = sum(c.weight for c in available)
    if used_weight <= 0:
        return PriorityScore(
            score=0.0, band=DEFAULT_BAND, version=PRIORITY_SCORE_VERSION,
            components=components,
            missing_inputs=[c.name for c in components],
            coverage=0.0,
        )

    weighted = sum((c.value or 0.0) * c.weight for c in available)
    score = 100.0 * weighted / used_weight
    return PriorityScore(
        score=score,
        band=band_for(score),
        version=PRIORITY_SCORE_VERSION,
        components=components,
        missing_inputs=[c.name for c in components if not c.available],
        coverage=(used_weight / total_weight) if total_weight else 0.0,
    )
