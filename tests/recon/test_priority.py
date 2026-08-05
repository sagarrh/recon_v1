# test_priority.py — the commercial priority score that replaced the SOV-derived dollar figure.
# The guarantees that matter: a missing input is never a zero, every score is explainable from its
# component vector, and nothing about the score is monetary.
from scout import priority as P

_FULL = {
    "shift_magnitude": 15.0,
    "client_sov_pp": 5.0,
    "search_impressions": 5000,
    "mapping_confidence": "exact",
    "evidence_confidence": "high",
    "action_type": "content_update",
    "client_readiness": {"confidence": "high", "llms_txt_present": True,
                         "robots_present": True, "schema_types_missing": []},
}


# ---------------------------------------------------------------------------
# Missing is not zero — the rule the whole module rests on.
# ---------------------------------------------------------------------------

def test_missing_input_is_excluded_not_scored_zero():
    """A client with GSC disconnected must not be pushed down the queue for lacking instrumentation.

    The score becomes the weighted average over what IS known, so it moves only as far as dropping
    that component shifts the average — nowhere near where scoring it zero would land it."""
    with_demand = P.score_signal(**_FULL)
    without_demand = P.score_signal(**{**_FULL, "search_impressions": None})
    scored_as_zero = P.score_signal(**{**_FULL, "search_impressions": 0})

    assert "search_demand" in without_demand.missing_inputs
    assert without_demand.coverage < with_demand.coverage   # scored on less evidence...
    # ...but sits beside the fully-evidenced score, not beside the zeroed one.
    assert abs(without_demand.score - with_demand.score) < 2.0
    assert without_demand.score - scored_as_zero.score > 15.0


def test_zero_demand_is_genuinely_different_from_missing_demand():
    measured_zero = P.score_signal(**{**_FULL, "search_impressions": 0})
    unknown = P.score_signal(**{**_FULL, "search_impressions": None})
    assert measured_zero.score < unknown.score
    assert "search_demand" not in measured_zero.missing_inputs


def test_score_with_no_inputs_at_all_is_zero_with_full_missing_list():
    out = P.score_signal(
        shift_magnitude=None, client_sov_pp=None, search_impressions=None,
        mapping_confidence="?", evidence_confidence="unknown", action_type="?",
        client_readiness=None,
    )
    assert out.score == 0.0
    assert out.band == "low"
    assert out.coverage == 0.0
    assert len(out.missing_inputs) == len(out.components)


def test_coverage_reports_how_much_evidence_backed_the_score():
    full = P.score_signal(**_FULL)
    assert full.coverage == 1.0
    partial = P.score_signal(**{**_FULL, "search_impressions": None, "client_sov_pp": None})
    assert 0.0 < partial.coverage < 1.0


# ---------------------------------------------------------------------------
# Ordering behaviour — what the score is actually for
# ---------------------------------------------------------------------------

def test_actionable_signal_outranks_an_unmappable_one():
    """An unmapped signal cannot be acted on or measured, so it must rank lower."""
    actionable = P.score_signal(**_FULL)
    unmapped = P.score_signal(**{**_FULL, "mapping_confidence": "unmapped"})
    assert actionable.score > unmapped.score


def test_cheaper_action_outranks_an_expensive_one_all_else_equal():
    cheap = P.score_signal(**{**_FULL, "action_type": "ai_access"})
    costly = P.score_signal(**{**_FULL, "action_type": "third_party"})
    assert cheap.score > costly.score


def test_stronger_competitive_move_outranks_a_weaker_one():
    big = P.score_signal(**{**_FULL, "shift_magnitude": 15.0})
    small = P.score_signal(**{**_FULL, "shift_magnitude": 1.0})
    assert big.score > small.score


def test_low_client_share_raises_the_gap_component_but_does_not_explode():
    """The old formula divided by share, so a 0.1pp client produced an unbounded number.
    Here a small share raises one bounded component and nothing more."""
    tiny = P.score_signal(**{**_FULL, "client_sov_pp": 0.1})
    large = P.score_signal(**{**_FULL, "client_sov_pp": 90.0})
    assert tiny.score > large.score
    assert tiny.score <= 100.0          # bounded, unlike revenue_opportunity


def test_score_is_always_within_bounds():
    for sov in (0.0, 0.1, 50.0, 100.0):
        for shift in (0.0, 1.0, 15.0, 500.0):
            out = P.score_signal(**{**_FULL, "client_sov_pp": sov, "shift_magnitude": shift})
            assert 0.0 <= out.score <= 100.0


# ---------------------------------------------------------------------------
# Auditability — a score must be explainable, not merely trusted
# ---------------------------------------------------------------------------

def test_every_component_publishes_a_reason():
    out = P.score_signal(**_FULL)
    assert all(c.reason for c in out.components)


def test_serialized_vector_carries_value_weight_and_reason_per_component():
    payload = P.score_signal(**_FULL).to_dict()
    assert payload["version"] == P.PRIORITY_SCORE_VERSION
    assert {c["name"] for c in payload["components"]} == set(P.DEFAULT_WEIGHTS)
    for component in payload["components"]:
        assert set(component) == {"name", "value", "weight", "reason"}


def test_missing_component_serializes_as_null_not_zero():
    payload = P.score_signal(**{**_FULL, "search_impressions": None}).to_dict()
    demand = next(c for c in payload["components"] if c["name"] == "search_demand")
    assert demand["value"] is None
    assert "search_demand" in payload["missing_inputs"]


def test_no_component_is_monetary():
    """If a dollar ever re-enters the ordering signal, it must fail here first."""
    payload = P.score_signal(**_FULL).to_dict()
    blob = str(payload).lower()
    for banned in ("usd", "revenue", "dollar", "aov", "conversion_rate"):
        assert banned not in blob


# ---------------------------------------------------------------------------
# Bands and operator tuning
# ---------------------------------------------------------------------------

def test_bands_follow_thresholds():
    assert P.band_for(95.0) == "critical"
    assert P.band_for(60.0) == "high"
    assert P.band_for(35.0) == "medium"
    assert P.band_for(5.0) == "low"


def test_operator_weights_change_the_score_and_are_recorded():
    default = P.score_signal(**_FULL)
    retuned = P.score_signal(**{**_FULL, "mapping_confidence": "unmapped"},
                             weights={**P.DEFAULT_WEIGHTS, "actionability": 10.0})
    baseline_unmapped = P.score_signal(**{**_FULL, "mapping_confidence": "unmapped"})
    # Heavier weight on a zero-valued component drags the score further down than the default would.
    assert retuned.score < baseline_unmapped.score < default.score
    actionability = next(c for c in retuned.components if c.name == "actionability")
    assert actionability.weight == 10.0
