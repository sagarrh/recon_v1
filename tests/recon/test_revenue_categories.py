# test_revenue_categories.py — the revenue evidence taxonomy and the guarantees it must hold.
# These tests exist to stop the SOV->dollar extrapolation from coming back: a category must be earned
# by observable linkage, never produced by arithmetic, and categories must never be added together.
import pytest

from scout import revenue as R

# ---------------------------------------------------------------------------
# Category resolution — evidence decides the grade, never the arithmetic.
# ---------------------------------------------------------------------------

def test_no_linkage_is_unavailable_not_zero():
    cat = R.resolve_revenue_category(page_scoped_revenue=None, linkage=R.LINKAGE_NONE)
    assert cat is R.RevenueCategory.unavailable


def test_exact_page_linkage_is_recorded():
    cat = R.resolve_revenue_category(page_scoped_revenue=1200.0, linkage=R.LINKAGE_EXACT_PAGE)
    assert cat is R.RevenueCategory.recorded


def test_session_linkage_is_influenced_not_recorded():
    cat = R.resolve_revenue_category(page_scoped_revenue=1200.0, linkage=R.LINKAGE_SESSION)
    assert cat is R.RevenueCategory.influenced


def test_control_adjustment_is_incremental_estimate():
    cat = R.resolve_revenue_category(page_scoped_revenue=1200.0, linkage=R.LINKAGE_EXACT_PAGE,
                                     control_adjusted=True)
    assert cat is R.RevenueCategory.incremental_estimate


def test_modeled_value_without_linkage_can_only_be_a_scenario():
    cat = R.resolve_revenue_category(page_scoped_revenue=None, linkage=R.LINKAGE_NONE,
                                     modeled_value=250_000.0)
    assert cat is R.RevenueCategory.modeled_scenario
    assert cat not in R.CLIENT_VISIBLE_CATEGORIES


def test_a_large_modeled_value_never_outranks_missing_linkage():
    """A big number must not buy a better grade — this is the defect the old formula had."""
    cat = R.resolve_revenue_category(page_scoped_revenue=None, linkage=R.LINKAGE_NONE,
                                     modeled_value=10_000_000.0)
    assert cat is R.RevenueCategory.modeled_scenario


# ---------------------------------------------------------------------------
# GA4 scoping — property-wide revenue is not a cluster's revenue.
# ---------------------------------------------------------------------------

_GA4_ROWS = [
    {"landing_page": "/procurement", "revenue": 500.0},
    {"landing_page": "/procurement", "revenue": 250.0},
    {"landing_page": "/unrelated-blog", "revenue": 9_000.0},
]


def test_ga4_revenue_requires_target_pages():
    assert R.actual_revenue_from_ga4(ga4_rows=_GA4_ROWS, landing_pages=set()) is None


def test_ga4_revenue_is_scoped_to_target_pages_only():
    total = R.actual_revenue_from_ga4(ga4_rows=_GA4_ROWS, landing_pages={"/procurement"})
    assert total == 750.0  # the 9,000 on an unrelated page must not be counted


def test_ga4_revenue_is_none_when_no_row_matches():
    assert R.actual_revenue_from_ga4(ga4_rows=_GA4_ROWS, landing_pages={"/nothing"}) is None


# ---------------------------------------------------------------------------
# Summation — there is deliberately no cross-category total.
# ---------------------------------------------------------------------------

def _findings():
    return [
        R.RevenueFinding(category=R.RevenueCategory.recorded, value_usd=100.0),
        R.RevenueFinding(category=R.RevenueCategory.recorded, value_usd=50.0),
        R.RevenueFinding(category=R.RevenueCategory.modeled_scenario, value_usd=900_000.0),
        R.RevenueFinding(category=R.RevenueCategory.unavailable, value_usd=None),
    ]


def test_sum_is_confined_to_one_category():
    assert R.sum_within_category(_findings(), R.RevenueCategory.recorded) == 150.0
    assert R.sum_within_category(_findings(), R.RevenueCategory.modeled_scenario) == 900_000.0


def test_unavailable_sums_to_none_not_zero():
    assert R.sum_within_category(_findings(), R.RevenueCategory.unavailable) is None


def test_module_exposes_no_cross_category_total():
    """The absence of these helpers is the enforcement mechanism — keep it that way."""
    for banned in ("total_revenue", "sum_all", "revenue_opportunity", "allocate_cluster_revenue"):
        assert not hasattr(R, banned), f"{banned} would permit an indefensible aggregate"


# ---------------------------------------------------------------------------
# Client visibility — a scenario is a planning figure, not a result.
# ---------------------------------------------------------------------------

def test_modeled_scenario_is_hidden_from_clients_by_default():
    visible = R.client_visible(_findings())
    assert all(f.category is not R.RevenueCategory.modeled_scenario for f in visible)


def test_modeled_scenario_appears_only_when_explicitly_enabled():
    visible = R.client_visible(_findings(), modeled_scenario_visible=True)
    assert any(f.category is R.RevenueCategory.modeled_scenario for f in visible)


def test_unavailable_is_never_client_visible():
    assert all(f.category is not R.RevenueCategory.unavailable
               for f in R.client_visible(_findings(), modeled_scenario_visible=True))


# ---------------------------------------------------------------------------
# Cluster assembly — the end-to-end grade.
# ---------------------------------------------------------------------------

_FINANCIALS = {"average_order_value": 400.0, "conversion_rate": 2.0,
               "estimated_ctr": 3.0, "currency": "USD"}


def test_cluster_without_target_pages_is_unavailable():
    out = R.compute_cluster_revenue(
        demand={"impressions": 100_000}, financials=_FINANCIALS,
        sov={"client_sov_pp": 5.0, "primary_competitor_sov_pp": 20.0},
        ga4=_GA4_ROWS, fx_rates={},
    )
    assert out["revenue_category"] == "unavailable"
    assert out["revenue_value_usd"] is None
    assert out["revenue_inputs"]["linkage"] == R.LINKAGE_NONE


def test_cluster_with_mapped_pages_is_recorded_and_page_scoped():
    out = R.compute_cluster_revenue(
        demand={"impressions": 100_000}, financials=_FINANCIALS,
        sov={"client_sov_pp": 5.0, "primary_competitor_sov_pp": 20.0},
        ga4=_GA4_ROWS, fx_rates={}, target_landing_pages={"/procurement"},
    )
    assert out["revenue_category"] == "recorded"
    assert out["revenue_value_usd"] == 750.0


@pytest.mark.parametrize("client_sov_pp", [0.1, 1.0, 5.0, 50.0])
def test_low_sov_no_longer_amplifies_the_figure(client_sov_pp):
    """The old formula divided by SOV, so a 0.1pp client reported ~999x its own revenue.

    The category result must now be independent of how small the client's share is."""
    out = R.compute_cluster_revenue(
        demand={"impressions": 100_000}, financials=_FINANCIALS,
        sov={"client_sov_pp": client_sov_pp, "primary_competitor_sov_pp": 20.0},
        ga4=_GA4_ROWS, fx_rates={}, target_landing_pages={"/procurement"},
    )
    assert out["revenue_value_usd"] == 750.0


def test_modeled_scenario_carries_its_assumptions():
    finding = R.modeled_value_scenario(
        impressions=100_000, client_ctr_pct=3.0, conversion_rate_pct=2.0,
        average_order_value=400.0, client_sov_share=5.0, capture_fraction=0.15,
    )
    assert finding.category is R.RevenueCategory.modeled_scenario
    assert not finding.is_client_visible
    assert any("capture fraction" in lim for lim in finding.limitations)
    assert any("not measured revenue" in lim for lim in finding.limitations)


def test_modeled_scenario_with_incomplete_inputs_is_unavailable():
    finding = R.modeled_value_scenario(
        impressions=None, client_ctr_pct=3.0, conversion_rate_pct=2.0,
        average_order_value=400.0, client_sov_share=5.0, capture_fraction=0.15,
    )
    assert finding.category is R.RevenueCategory.unavailable
    assert finding.value_usd is None
