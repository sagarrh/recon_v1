"""Target mapping and outcome classification.

The rules under test are the ones that make a measurement trustworthy: never accept a fuzzy match,
never join on path alone, never claim causation, and never dress up noise as a result.
"""

import pytest

from ai_visibility.measurement import mapping as M
from ai_visibility.measurement import outcomes as O

SOURCE_QUERIES = ["enterprise procurement platform", "procurement software", "best crm"]
SOURCE_PAGES = ["https://multiplierai.ai/procurement", "https://multiplierai.ai/pricing"]


# ---------------------------------------------------------------------------
# Mapping — exact or normalized-exact only.
# ---------------------------------------------------------------------------

def test_normalized_exact_query_match_is_accepted():
    result = M.map_targets(
        target_queries=["  Enterprise   Procurement Platform "], target_pages=[],
        source_queries=SOURCE_QUERIES, source_pages=SOURCE_PAGES,
    )
    assert result.queries[0].status == M.NORMALIZED
    assert result.matched_queries == ["enterprise procurement platform"]


def test_near_miss_is_a_candidate_never_a_match():
    """A substring overlap is not evidence the client targeted that query."""
    result = M.map_targets(
        target_queries=["procurement"], target_pages=[],
        source_queries=SOURCE_QUERIES, source_pages=SOURCE_PAGES,
    )
    mapped = result.queries[0]
    assert mapped.status == M.UNMAPPED
    assert not mapped.usable
    assert mapped.candidates                      # surfaced for a human...
    assert result.matched_queries == []           # ...but never counted


def test_unrelated_query_maps_to_nothing():
    result = M.map_targets(
        target_queries=["completely unrelated topic"], target_pages=[],
        source_queries=SOURCE_QUERIES, source_pages=SOURCE_PAGES,
    )
    assert result.queries[0].status == M.UNMAPPED
    assert result.queries[0].candidates == ()


def test_page_matching_keeps_the_host():
    """Joining on path alone would merge two clients' /pricing pages on a shared property."""
    assert M.normalize_page("https://a.com/pricing") != M.normalize_page("https://b.com/pricing")
    assert M.normalize_page("https://www.a.com/pricing/") == M.normalize_page("http://a.com/pricing")


def test_page_target_matches_across_spelling_variants():
    result = M.map_targets(
        target_queries=[], target_pages=["http://www.multiplierai.ai/procurement/"],
        source_queries=SOURCE_QUERIES, source_pages=SOURCE_PAGES,
    )
    assert result.pages[0].usable
    assert result.matched_pages == ["https://multiplierai.ai/procurement"]


def test_page_outside_owned_domains_is_rejected_as_cross_tenant():
    result = M.map_targets(
        target_queries=[], target_pages=["https://competitor.com/procurement"],
        source_queries=SOURCE_QUERIES, source_pages=SOURCE_PAGES,
        owned_page_check=lambda url: "multiplierai.ai" in url,
    )
    assert result.pages[0].status == M.REJECTED_CROSS_TENANT
    assert result.matched_pages == []


def test_no_targets_means_no_measurable_scope():
    result = M.map_targets(
        target_queries=[], target_pages=[],
        source_queries=SOURCE_QUERIES, source_pages=SOURCE_PAGES,
    )
    assert not result.has_any_target


# ---------------------------------------------------------------------------
# Outcome classification — conservative, and never causal.
# ---------------------------------------------------------------------------

def _delta(metric, baseline, follow_up, absolute, relative):
    return {"metric": metric, "baseline": baseline, "follow_up": follow_up,
            "absolute_delta": absolute, "relative_delta": relative}


_GROWTH = [
    _delta("impressions", 1000, 1400, 400, 0.40),
    _delta("clicks", 100, 130, 30, 0.30),
    _delta("average_position", 11.2, 7.8, -3.4, -0.30),
]
_NOISE = [
    _delta("impressions", 1000, 1030, 30, 0.03),
    _delta("clicks", 100, 102, 2, 0.02),
    _delta("average_position", 11.2, 11.0, -0.2, -0.02),
]


def _classify(deltas, **over):
    kwargs = {"baseline_impressions": 1000, "window_status": "ready", "source_status": "available"}
    kwargs.update(over)
    return O.classify_gsc(deltas, **kwargs)


def test_growth_across_several_metrics_is_associated_growth():
    out = _classify(_GROWTH)
    assert out.classification == O.SEARCH_GROWTH
    assert out.confidence == "medium"          # multiple metrics agreeing
    assert out.evidence["material_improvements"] == 3


def test_small_moves_are_noise_not_a_result():
    out = _classify(_NOISE)
    assert out.classification == O.NO_MATERIAL_CHANGE


def test_thin_baseline_is_insufficient_data_not_a_percentage():
    out = _classify(_GROWTH, baseline_impressions=10)
    assert out.classification == O.INSUFFICIENT_DATA
    assert any("below the" in limit for limit in out.limitations)


def test_conflicting_metrics_are_mixed_with_low_confidence():
    deltas = [
        _delta("impressions", 1000, 1400, 400, 0.40),
        _delta("clicks", 100, 60, -40, -0.40),
    ]
    out = _classify(deltas)
    assert out.classification == O.MIXED
    assert out.confidence == "low"


def test_position_direction_is_inverted_when_judging_materiality():
    """Position rising from 7.8 to 11.2 is a decline, even though the number grew."""
    out = _classify([_delta("average_position", 7.8, 11.2, 3.4, 0.44)])
    assert out.classification == O.SEARCH_DECLINE


def test_sub_threshold_position_move_is_not_material():
    out = _classify([_delta("average_position", 11.2, 10.5, -0.7, -0.06)])
    assert out.classification == O.NO_MATERIAL_CHANGE


def test_waiting_window_reports_waiting_not_a_flat_result():
    out = _classify(_GROWTH, window_status="waiting_for_window")
    assert out.classification == O.WAITING_FOR_WINDOW
    assert out.confidence == "none"


def test_missing_anchor_is_unmapped_not_measured():
    out = _classify(_GROWTH, window_status="no_anchor")
    assert out.classification == O.UNMAPPED


def test_refused_shared_handle_is_reported_as_source_unavailable():
    out = _classify(_GROWTH, source_status="shared_handle")
    assert out.classification == O.SOURCE_NOT_CONNECTED
    assert "refused" in out.evidence["reason"]


def test_unmapped_source_never_becomes_a_measured_result():
    out = _classify(_GROWTH, source_status="unmapped")
    assert out.classification == O.UNMAPPED


@pytest.mark.parametrize("window,source", [
    ("ready", "available"), ("waiting_for_window", "available"), ("ready", "unmapped"),
])
def test_every_outcome_carries_its_limitations(window, source):
    """A result without its limits reads as more certain than it is."""
    out = _classify(_GROWTH, window_status=window, source_status=source)
    assert len(out.limitations) >= len(O.BASE_LIMITATIONS)
    assert any("no control" in limit for limit in out.limitations)


def test_no_classification_claims_causation():
    for name in vars(O).values():
        if isinstance(name, str) and name.islower() and "_" in name:
            assert "caused" not in name and "because" not in name
    out = _classify(_GROWTH)
    assert "associated" in out.classification
    assert any("does not establish that the action caused" in limit for limit in out.limitations)
