"""Outcome cards and the execution funnel.

The rules under test are about honesty rather than arithmetic: a missing source must never read as
a zero result, confidence must never reach "high" without a control, and the positive rate must
never be reportable without the null and negative rates beside it.
"""

from aivc.reporting import funnel as F
from aivc.reporting import outcome_cards as OC

_GROWTH_DELTAS = [
    {"metric": "impressions", "direction": "improved", "absolute_delta": 400.0,
     "relative_delta": 0.40},
    {"metric": "average_position", "direction": "improved", "absolute_delta": -3.4,
     "relative_delta": -0.30},
]


def _row(**over):
    base = {
        "recommendation_id": "r1", "cluster_label": "AI Marketing Automation",
        "implemented_at": None, "implemented_by": "cs@example.com",
        "baseline_client_sov_pp": 24.0, "current_client_sov_pp": 39.0, "sov_delta_pp": 15.0,
        "classification": "associated_search_growth", "gsc_deltas": _GROWTH_DELTAS,
        "limitations": ["before/after comparison with no control page or topic group"],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# A missing source is never a zero result.
# ---------------------------------------------------------------------------

def test_uninstrumented_layers_are_unavailable_not_zero():
    card = OC.build_card(_row())
    by_name = {layer.name: layer for layer in card.layers}
    for name in (OC.WEBSITE_ENGAGEMENT, OC.COMMERCIAL_INTENT, OC.REVENUE):
        assert by_name[name].status == OC.UNAVAILABLE
        assert by_name[name].detail                      # and it says WHY
        assert "not connected" in by_name[name].detail or "no ecommerce" in by_name[name].detail


def test_every_layer_appears_even_when_unavailable():
    """Omitting a layer would let a reader assume it was fine."""
    card = OC.build_card(_row())
    assert [layer.name for layer in card.layers] == list(OC.LAYER_ORDER)


def test_insufficient_search_data_is_unavailable_not_a_flat_result():
    card = OC.build_card(_row(classification="insufficient_data", gsc_deltas=[]))
    search = next(x for x in card.layers if x.name == OC.SEARCH_DEMAND)
    assert search.status == OC.UNAVAILABLE
    assert "too little search volume" in search.detail


def test_refused_shared_property_explains_itself_on_the_card():
    card = OC.build_card(_row(classification="source_not_connected", gsc_deltas=[]))
    search = next(x for x in card.layers if x.name == OC.SEARCH_DEMAND)
    assert search.status == OC.UNAVAILABLE
    assert "shared with another client" in search.detail


def test_unmeasured_visibility_is_unavailable():
    card = OC.build_card(_row(sov_delta_pp=None, current_client_sov_pp=None))
    visibility = next(x for x in card.layers if x.name == OC.AI_VISIBILITY)
    assert visibility.status == OC.UNAVAILABLE


# ---------------------------------------------------------------------------
# Assessment and confidence.
# ---------------------------------------------------------------------------

def test_two_evidenced_layers_improving_reads_as_associated_growth():
    card = OC.build_card(_row())
    assert card.assessment == "associated downstream growth"
    assert card.confidence == "medium"


def test_confidence_never_reaches_high_without_a_control():
    """Whatever the numbers look like, a single uncontrolled window cannot support "high"."""
    card = OC.build_card(_row())
    assert card.confidence != "high"


def test_conflicting_layers_read_as_mixed():
    card = OC.build_card(_row(sov_delta_pp=-12.0, current_client_sov_pp=12.0))
    assert card.assessment == "mixed result"
    assert card.confidence == "low"


_INTERNALLY_MIXED_DELTAS = [
    {"metric": "impressions", "direction": "declined", "absolute_delta": -83.0,
     "relative_delta": -0.57},
    {"metric": "average_position", "direction": "improved", "absolute_delta": -18.8,
     "relative_delta": -0.44},
]


def test_a_layer_whose_own_metrics_disagree_is_mixed_not_no_change():
    """Regression: a `mixed` layer is neither improved nor declined, and previously fell through
    to "no material change" with MEDIUM confidence — understating a real movement and overstating
    certainty about it. Ranking improved 18 places while impressions more than halved; that is a
    mixed result, not an absence of one."""
    card = OC.build_card(_row(
        classification="mixed_result",
        gsc_deltas=_INTERNALLY_MIXED_DELTAS,
        sov_delta_pp=None, current_client_sov_pp=None,   # visibility unavailable
    ))
    search = next(x for x in card.layers if x.name == OC.SEARCH_DEMAND)
    assert search.status == OC.MIXED
    assert card.assessment == "mixed result"
    assert card.confidence == "low"


def test_genuinely_flat_metrics_still_read_as_no_material_change():
    """The fix must not swallow the real no-change case."""
    card = OC.build_card(_row(
        classification="no_material_change",
        gsc_deltas=[{"metric": "impressions", "direction": "flat",
                     "absolute_delta": 2.0, "relative_delta": 0.01}],
        sov_delta_pp=0.1, current_client_sov_pp=24.1, baseline_client_sov_pp=24.0,
    ))
    assert card.assessment == "no material change"


def test_card_always_explains_why_confidence_is_not_higher():
    card = OC.build_card(_row())
    assert card.why_confidence_is_not_higher
    assert any("no control" in reason for reason in card.why_confidence_is_not_higher)
    assert any("unavailable" in reason for reason in card.why_confidence_is_not_higher)


def test_visibility_is_rendered_as_a_before_and_after():
    card = OC.build_card(_row())
    visibility = next(x for x in card.layers if x.name == OC.AI_VISIBILITY)
    assert "24.0% → 39.0%" in visibility.detail
    assert "+15.0pp" in visibility.detail


# ---------------------------------------------------------------------------
# Funnel and north star.
# ---------------------------------------------------------------------------

def _funnel(**over):
    base = {"proposed": 20, "accepted": 10, "executed": 8, "verified": 3,
            "measured": 4, "positive": 2, "null_result": 1, "negative": 1, "pending": 2}
    base.update(over)
    return F.ExecutionFunnel(**base)


def test_north_star_is_positive_over_executed_not_over_measured():
    """Dividing by measured would let poor coverage inflate the headline."""
    out = _funnel()
    assert out.north_star == 2 / 8
    assert out.positive_rate_of_measured == 2 / 4
    assert out.north_star < out.positive_rate_of_measured


def test_null_and_negative_are_reported_beside_the_positive_rate():
    payload = _funnel().as_dict()
    assert payload["results"]["positive"] == 2
    assert payload["results"]["no_material_change_or_mixed"] == 1
    assert payload["results"]["negative"] == 1


def test_pending_items_are_excluded_from_rates_not_counted_as_failures():
    payload = _funnel().as_dict()
    assert payload["results"]["pending_or_unmeasurable"] == 2
    # measured (4) excludes the 2 pending; north star divides by executed, not by proposed
    assert _funnel().measurement_rate == 4 / 8


def test_rates_are_none_not_zero_when_there_is_no_denominator():
    """An undefined rate is not a rate of 0% — reporting 0% would imply nothing worked."""
    empty = _funnel(proposed=0, accepted=0, executed=0, verified=0,
                    measured=0, positive=0, null_result=0, negative=0, pending=0)
    assert empty.north_star is None
    assert empty.execution_rate is None
    assert empty.measurement_rate is None


def test_execution_drop_off_is_visible():
    """If most recommendations are never executed, that is the finding."""
    out = _funnel(proposed=100, executed=5)
    assert out.execution_rate == 0.05


def test_classification_sets_do_not_overlap():
    sets = (F.POSITIVE_CLASSIFICATIONS, F.NEGATIVE_CLASSIFICATIONS,
            F.NULL_CLASSIFICATIONS, F.PENDING_CLASSIFICATIONS)
    for i, first in enumerate(sets):
        for second in sets[i + 1:]:
            assert not (first & second)
