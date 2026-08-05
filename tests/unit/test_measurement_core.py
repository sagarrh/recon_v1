"""The pure core of outcome measurement: tenant scoping, window construction, aggregation.

Each test here pins a rule that produces a plausible-looking wrong answer when broken — which is
the dangerous kind, because nobody questions a number that moved in a believable direction.
"""

from datetime import date
from uuid import UUID

import pytest

from ai_visibility.measurement import aggregation as A
from ai_visibility.measurement import tenancy as T
from ai_visibility.measurement import windows as W

CLIENT = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
OWNED = frozenset({"vibe-engine.ai"})


# ---------------------------------------------------------------------------
# Tenancy — GSC is keyed by site_url, GA4 by property_id; neither is a client.
# ---------------------------------------------------------------------------

def _scope(**over):
    kwargs = {
        "handles": {"gsc_site_url": "sc-domain:vibe-engine.ai", "ga4_property_id": "543007418"},
        "owned_domains": OWNED,
        "handle_owners": {},
    }
    kwargs.update(over)
    return T.resolve_tenant_scope(CLIENT, **kwargs)


def test_unshared_handles_are_measurable():
    scope = _scope()
    assert scope.gsc_measurable and scope.ga4_measurable
    assert scope.warnings == []


def test_shared_handle_is_refused_by_default():
    """Two clients on one property: reading it for either would report the other's performance."""
    scope = _scope(handle_owners={"sc-domain:vibe-engine.ai": {str(CLIENT).casefold(),
                                                               str(OTHER).casefold()}})
    assert scope.gsc_status == T.SHARED_HANDLE
    assert not scope.gsc_measurable
    assert scope.ga4_measurable                      # the unshared handle is unaffected
    assert any("refusing" in w for w in scope.warnings)


def test_explicit_override_permits_a_shared_handle_with_domain_scoping():
    scope = _scope(
        handle_owners={"sc-domain:vibe-engine.ai": {str(CLIENT).casefold(),
                                                    str(OTHER).casefold()}},
        shared_handle_overrides=frozenset({str(CLIENT).casefold()}),
    )
    assert scope.gsc_measurable
    assert scope.requires_domain_scoping is True     # scoping is now mandatory, not optional
    assert scope.shared_with == (str(OTHER).casefold(),)


def test_override_is_refused_when_there_is_no_domain_to_scope_by():
    """Domain scoping is the only thing separating two clients on one handle."""
    scope = _scope(
        owned_domains=frozenset(),
        handle_owners={"sc-domain:vibe-engine.ai": {str(CLIENT).casefold(),
                                                    str(OTHER).casefold()}},
        shared_handle_overrides=frozenset({str(CLIENT).casefold()}),
    )
    assert scope.gsc_status == T.SHARED_HANDLE
    assert scope.requires_domain_scoping is False
    assert any("no registered domain" in w for w in scope.warnings)


def test_excluded_client_is_never_measurable():
    scope = _scope(excluded_client_ids=frozenset({str(CLIENT).casefold()}))
    assert scope.gsc_status == scope.ga4_status == T.EXCLUDED
    assert not scope.gsc_measurable and not scope.ga4_measurable


def test_client_with_no_user_metrics_row_reports_unknown_not_zero():
    scope = _scope(handles=None)
    assert scope.gsc_status == T.UNKNOWN_CLIENT
    assert not scope.gsc_measurable


def test_missing_handle_is_no_handle_not_an_error():
    scope = _scope(handles={"gsc_site_url": "", "ga4_property_id": "543007418"})
    assert scope.gsc_status == T.NO_HANDLE
    assert scope.ga4_measurable


def test_domain_mismatch_on_an_unshared_handle_warns_but_still_measures():
    """Sharing is the attribution risk. An unshared property is unambiguously this client's,
    even when the registered domain was entered wrong."""
    scope = _scope(owned_domains=frozenset({"gmail.com"}))
    assert scope.gsc_measurable
    assert any("outside the client's registered domains" in w for w in scope.warnings)


def test_sc_domain_prefix_and_subdomains_resolve_correctly():
    assert T.host_matches_owned("sc-domain:vibe-engine.ai", OWNED)
    assert T.host_matches_owned("https://blog.vibe-engine.ai/x", OWNED)
    assert not T.host_matches_owned("https://notvibe-engine.ai/x", OWNED)


# ---------------------------------------------------------------------------
# Handle ownership — an excluded account must not make a property look shared.
# ---------------------------------------------------------------------------

_REAL = str(CLIENT).casefold()
_TEST = str(OTHER).casefold()
_ROWS_SHARED_WITH_TEST_ACCOUNT = [
    {"client_id": _REAL, "gsc_site_url": "sc-domain:x.ai", "ga4_property_id": "1"},
    {"client_id": _TEST, "gsc_site_url": "sc-domain:x.ai", "ga4_property_id": "1"},
]


def test_excluded_account_does_not_make_a_property_shared():
    """Otherwise the exclusion list could never unblock anything — the real owner of a property
    a test account is attached to would stay permanently refused."""
    owners = T.build_handle_owners(
        _ROWS_SHARED_WITH_TEST_ACCOUNT, excluded_client_ids=frozenset({_TEST})
    )
    assert owners["sc-domain:x.ai"] == {_REAL}

    scope = T.resolve_tenant_scope(
        CLIENT,
        handles={"gsc_site_url": "sc-domain:x.ai", "ga4_property_id": "1"},
        owned_domains=frozenset({"x.ai"}),
        handle_owners=owners,
        excluded_client_ids=frozenset({_TEST}),
    )
    assert scope.gsc_measurable and scope.ga4_measurable


def test_two_real_clients_on_one_property_are_still_refused():
    """Exclusions clear test noise, not genuine collisions between two live clients."""
    owners = T.build_handle_owners(_ROWS_SHARED_WITH_TEST_ACCOUNT)
    assert owners["sc-domain:x.ai"] == {_REAL, _TEST}

    scope = T.resolve_tenant_scope(
        CLIENT,
        handles={"gsc_site_url": "sc-domain:x.ai", "ga4_property_id": "1"},
        owned_domains=frozenset({"x.ai"}),
        handle_owners=owners,
    )
    assert scope.gsc_status == T.SHARED_HANDLE


def test_rows_without_handles_contribute_no_ownership():
    owners = T.build_handle_owners([
        {"client_id": _REAL, "gsc_site_url": "", "ga4_property_id": None},
    ])
    assert owners == {}


# ---------------------------------------------------------------------------
# Windows — anchored on implementation, equal length, complete.
# ---------------------------------------------------------------------------

def test_no_anchor_means_no_window():
    window = W.build_window(None, today=date(2026, 11, 1))
    assert window.status == W.NO_ANCHOR
    assert not window.ready
    assert "may never have shipped" in window.reason


def test_windows_are_equal_length_and_exclude_the_anchor_day():
    """The change ships mid-day, so the anchor belongs to neither side of its own comparison."""
    window = W.build_window(date(2026, 6, 1), today=date(2026, 9, 1), window_days=28)
    assert window.baseline_start == date(2026, 5, 4)
    assert window.baseline_end == date(2026, 5, 31)     # day before the anchor
    assert window.follow_up_start == date(2026, 6, 2)   # day after the anchor
    assert window.follow_up_end == date(2026, 6, 29)
    assert (window.baseline_end - window.baseline_start) == (
        window.follow_up_end - window.follow_up_start
    )
    assert window.ready


def test_incomplete_follow_up_waits_rather_than_reporting_a_partial_result():
    window = W.build_window(date(2026, 10, 20), today=date(2026, 11, 1), window_days=28)
    assert window.status == W.WAITING_FOR_WINDOW
    assert not window.ready
    assert "more day(s) needed" in window.reason


def test_actual_source_freshness_overrides_the_assumed_lag():
    """The configured lag is a policy guess; what the source holds is a fact.

    Observed here: the assumed 2-day lag put fresh_through at 08-03 while the source only held
    complete data through 08-01. Trusting the assumption marks a window settled with its final
    days missing, which reads as a decline that has not happened."""
    # Anchor chosen so the follow-up window ends exactly on the assumed freshness boundary:
    # ready under the assumption, not ready against what the source actually holds.
    anchor = date(2026, 7, 6)
    optimistic = W.build_window(anchor, today=date(2026, 8, 5), window_days=28, source_lag_days=2)
    assert optimistic.fresh_through == date(2026, 8, 3)
    assert optimistic.follow_up_end == date(2026, 8, 3)
    assert optimistic.ready                      # would measure a window it cannot fully cover

    grounded = W.build_window(
        anchor, today=date(2026, 8, 5), window_days=28, source_lag_days=2,
        source_fresh_through=date(2026, 8, 1),
    )
    assert grounded.fresh_through == date(2026, 8, 1)
    assert grounded.status == W.WAITING_FOR_WINDOW


def test_the_earlier_of_assumed_and_actual_freshness_wins():
    """A source running AHEAD of the policy lag must not shorten the settle period either."""
    window = W.build_window(
        date(2026, 6, 1), today=date(2026, 8, 5), window_days=28, source_lag_days=2,
        source_fresh_through=date(2026, 8, 4),   # fresher than today-2
    )
    assert window.fresh_through == date(2026, 8, 3)


def test_google_two_day_lag_is_enforced():
    """A follow-up ending today is not yet settled: both integrations clamp to today-2."""
    anchor = date(2026, 6, 1)
    ends_today = W.build_window(anchor, today=date(2026, 6, 29), window_days=28)
    assert ends_today.status == W.WAITING_FOR_WINDOW
    settled = W.build_window(anchor, today=date(2026, 7, 1), window_days=28)
    assert settled.ready
    assert settled.fresh_through == date(2026, 6, 29)


# ---------------------------------------------------------------------------
# Aggregation — the two rules that fail convincingly.
# ---------------------------------------------------------------------------

_ROWS = [
    {"query": "a", "page": "/p1", "impressions": 1000, "clicks": 10, "position": 10.0},
    {"query": "b", "page": "/p1", "impressions": 2, "clicks": 2, "position": 1.0},
]


def test_ctr_is_recomputed_from_totals_not_averaged_across_rows():
    """Row-level averaging gives a 2-impression query the same say as a 1000-impression one:
    it would report (1% + 100%)/2 = 50.5% instead of the true 1.2%."""
    totals = A.aggregate_gsc_rows(_ROWS)
    assert totals.ctr_percent == pytest.approx(100.0 * 12 / 1002)
    assert totals.ctr_percent < 2.0


def test_average_position_is_impression_weighted_not_a_flat_mean():
    """A flat mean would report (10.0 + 1.0)/2 = 5.5, implying a far better ranking than reality."""
    totals = A.aggregate_gsc_rows(_ROWS)
    assert totals.average_position == pytest.approx((10.0 * 1000 + 1.0 * 2) / 1002)
    assert totals.average_position > 9.9


def test_rows_without_a_position_do_not_drag_the_average_toward_zero():
    """Treating a missing position as 0 would read as a top-of-page ranking."""
    rows = [*_ROWS, {"query": "c", "page": "/p2", "impressions": 500, "clicks": 0}]
    totals = A.aggregate_gsc_rows(rows)
    assert totals.average_position == pytest.approx((10.0 * 1000 + 1.0 * 2) / 1002)
    assert totals.impressions == 1502          # still counted in the volume totals


def test_empty_window_reports_none_not_zero_for_ratio_metrics():
    totals = A.aggregate_gsc_rows([])
    assert totals.impressions == 0
    assert totals.ctr_percent is None          # no impressions means undefined, not 0%
    assert totals.average_position is None


def test_zero_baseline_keeps_the_absolute_delta_and_nulls_the_ratio():
    out = A.delta("impressions", 0, 250, unit=A.UNIT_COUNT)
    assert out["absolute_delta"] == 250
    assert out["relative_delta"] is None       # never infinite, never capped
    assert out["note"] == "new activity from a zero baseline"


def test_average_position_direction_is_inverted():
    """Position 11.2 -> 7.8 is an improvement even though the number fell."""
    out = A.delta("average_position", 11.2, 7.8, unit=A.UNIT_POSITION)
    assert out["absolute_delta"] == pytest.approx(-3.4)
    assert out["direction"] == "improved"
    assert A.delta("clicks", 10, 5, unit=A.UNIT_COUNT)["direction"] == "declined"


def test_unmeasurable_metric_is_unavailable_not_zero():
    out = A.delta("ctr_percent", None, 3.2, unit=A.UNIT_PERCENT)
    assert out["direction"] == "unavailable"
    assert out["absolute_delta"] is None


def test_compare_windows_covers_the_full_gsc_metric_set():
    metrics = {d["metric"] for d in A.compare_windows(
        A.aggregate_gsc_rows(_ROWS), A.aggregate_gsc_rows(_ROWS)
    )}
    assert metrics == {"impressions", "clicks", "ctr_percent", "average_position"}


# ---------------------------------------------------------------------------
# GA4 on a shared property: separable or not?
# ---------------------------------------------------------------------------

def test_ga4_path_only_landing_pages_cannot_identify_a_client():
    """GA4 stores landing_page as a path. On a shared property '/pricing' could belong to either
    client, so there is nothing to scope by — the override cannot be honoured safely."""
    from ai_visibility.measurement.ga4 import _has_host

    assert not _has_host("/")
    assert not _has_host("/insights")
    assert not _has_host("/resources/ai-search-revenue-attribution")
    assert _has_host("https://multiplierai.ai/insights")
    assert _has_host("multiplierai.ai/insights")


def test_ga4_engagement_rate_is_recomputed_from_totals():
    """Same trap as CTR: a per-row average lets a 2-session page outweigh a 500-session one."""
    rows = [
        {"landing_page": "/a", "sessions": 500, "engaged_sessions": 100},
        {"landing_page": "/b", "sessions": 2, "engaged_sessions": 2},
    ]
    totals = A.aggregate_ga4_rows(rows)
    assert totals.sessions == 502
    assert totals.engagement_rate_percent == pytest.approx(100.0 * 102 / 502)
    assert totals.engagement_rate_percent < 25.0     # a flat mean would report ~60%


def test_ga4_empty_window_reports_none_not_zero_rate():
    totals = A.aggregate_ga4_rows([])
    assert totals.sessions == 0
    assert totals.engagement_rate_percent is None
