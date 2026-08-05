# test_revenue_guardrails.py — behavioural guardrails for the revenue-evidence direction.
# test_revenue_categories.py covers the taxonomy itself; this file covers the three places the old
# model leaked into behaviour: client-facing prose, asset-level allocation, and triage ordering.
import re
import types

import scout.builders.handoff as HO
import scout.db.outcome_measure as OM
import scout.nodes.triage_digest as TD
import scout.reports.asset_attribution as AA
from scout import revenue as R
from scout.models.sov import ClusterVerdict

# Any currency-looking token. The old prose emitted "$250,000 [modeled]".
_MONEY = re.compile(r"[$₹€£]\s*[\d,]+|\b\d{1,3}(?:,\d{3})+\b")


# ---------------------------------------------------------------------------
# Client-facing prose must never quote a revenue figure.
# Nothing is implemented or measured at hand-off time, so any number is a projection.
# ---------------------------------------------------------------------------

def test_handoff_rationale_quotes_no_money_for_legacy_shaped_rows():
    line = HO.revenue_rationale_line({
        "cluster_label": "enterprise procurement",
        "revenue_opportunity_usd": 250_000,
        "revenue_basis": "modeled",
    })
    assert not _MONEY.search(line), line
    assert "enterprise procurement" in line


def test_handoff_rationale_quotes_no_money_for_new_shaped_rows():
    line = HO.revenue_rationale_line({
        "cluster_label": "enterprise procurement",
        "revenue_value_usd": 250_000,
        "revenue_category": "recorded",
    })
    assert not _MONEY.search(line), line


def test_handoff_rationale_survives_an_empty_row():
    line = HO.revenue_rationale_line({})
    assert not _MONEY.search(line)
    assert line.strip()


# ---------------------------------------------------------------------------
# Asset attribution: a dollar requires an observable link, and cluster revenue
# is never divided between assets.
# ---------------------------------------------------------------------------

_CFG = types.SimpleNamespace(
    geo_ga4_enabled=True,
    builder_attribution_link_enabled=False,
    asset_attribution_channel_weights={"ga4_landing_page": 1.0},
    asset_modeled_discount=0.6,
)

_OUTCOME = {
    "client_id": "c1", "cluster_id": "clu1",
    "week_date": "2026-06-01", "window_elapsed_at": "2026-06-29",
    "window_weeks": 4, "revenue_delta_usd": 1000.0, "measured_at": "2026-06-30",
}

# One asset has an exact GA4 landing page; the other does not.
_LINKED = {"id": "a-linked", "client_id": "c1", "cluster_id": "clu1",
           "content_url": "https://client.com/procurement", "asset_status": "published",
           "asset_source": "target", "recommendation_id": "r1"}
_UNLINKED = {"id": "a-unlinked", "client_id": "c1", "cluster_id": "clu1",
             "content_url": "https://client.com/no-ga4-data", "asset_status": "published",
             "asset_source": "target", "recommendation_id": "r1"}

_GA4 = [
    {"landing_page": "/procurement", "metric_date": "2026-06-15", "revenue": 900.0},
    {"landing_page": "/procurement", "metric_date": "2026-05-10", "revenue": 400.0},
]


def _patch_fetchers(monkeypatch, *, assets, ga4=None, selection=None):
    monkeypatch.setattr(AA, "get_config", lambda: _CFG)
    monkeypatch.setattr(AA, "_fetch_assets", lambda sb, cid=None: assets)
    monkeypatch.setattr(AA, "_fetch_measured_outcomes", lambda sb, cid=None, since=None: [_OUTCOME])
    monkeypatch.setattr(AA, "_fetch_ga4_rows", lambda sb, cid, weeks=26: ga4 or [])
    monkeypatch.setattr(AA, "_fetch_selection_events", lambda sb, cid: selection or [])
    monkeypatch.setattr(AA, "_fetch_registry", lambda sb, cid: {"clu1": {"queries": ["procurement"]}})


def test_exact_ga4_page_match_earns_a_dollar(monkeypatch):
    _patch_fetchers(monkeypatch, assets=[_LINKED], ga4=_GA4)
    rows = AA.build_asset_attribution(object())
    linked = [r for r in rows if r["scout_asset_id"] == "a-linked"]
    assert len(linked) == 1
    assert linked[0]["attributed_revenue_usd"] == 500.0     # 900 current - 400 baseline
    assert linked[0]["attribution_status"] == "attributed"
    assert linked[0]["revenue_category"] == "recorded"


def test_asset_without_page_match_gets_null_not_a_share(monkeypatch):
    """The anti-allocation test: two assets in one cluster, one linked. The other gets NULL.

    The old code split the remaining cluster pool between eligible published assets (or handed the
    whole pool to a single one). Both behaviours are gone."""
    _patch_fetchers(monkeypatch, assets=[_LINKED, _UNLINKED], ga4=_GA4)
    rows = AA.build_asset_attribution(object())
    unlinked = [r for r in rows if r["scout_asset_id"] == "a-unlinked"]
    assert len(unlinked) == 1
    assert unlinked[0]["attributed_revenue_usd"] is None
    assert unlinked[0]["attribution_status"] == R.LINKAGE_NONE
    assert unlinked[0]["revenue_category"] == "unavailable"


def test_cluster_revenue_is_never_split_across_assets(monkeypatch):
    """No asset without its own GA4 match may carry a dollar, however much cluster revenue exists."""
    selection = [{"was_selected": True, "query_text": "procurement", "revenue_attributed": 50_000.0}]
    _patch_fetchers(monkeypatch, assets=[_LINKED, _UNLINKED], ga4=_GA4, selection=selection)
    rows = AA.build_asset_attribution(object())
    dollars = {r["scout_asset_id"]: r["attributed_revenue_usd"] for r in rows}
    assert dollars["a-linked"] == 500.0      # its own measured page delta, not a share of 50,000
    assert dollars["a-unlinked"] is None


def test_cluster_ai_channel_revenue_is_context_not_attribution(monkeypatch):
    """Observed cluster-level revenue is retained for visibility but licenses no asset dollar."""
    selection = [{"was_selected": True, "query_text": "procurement", "revenue_attributed": 50_000.0}]
    _patch_fetchers(monkeypatch, assets=[_UNLINKED], ga4=_GA4, selection=selection)
    rows = AA.build_asset_attribution(object())
    inputs = rows[0]["revenue_inputs"]
    assert inputs["cluster_ai_channel_revenue_observed"] == 50_000.0
    assert rows[0]["attributed_revenue_usd"] is None


def test_every_asset_yields_exactly_one_row(monkeypatch):
    """Coverage KPIs need a denominator: one row per asset, no duplicates, no silent drops."""
    _patch_fetchers(monkeypatch, assets=[_LINKED, _UNLINKED], ga4=_GA4)
    rows = AA.build_asset_attribution(object())
    ids = [r["scout_asset_id"] for r in rows]
    assert sorted(ids) == ["a-linked", "a-unlinked"]


def test_no_ga4_data_means_no_dollars_anywhere(monkeypatch):
    _patch_fetchers(monkeypatch, assets=[_LINKED, _UNLINKED], ga4=[])
    rows = AA.build_asset_attribution(object())
    assert all(r["attributed_revenue_usd"] is None for r in rows)
    assert all(r["revenue_category"] == "unavailable" for r in rows)


# ---------------------------------------------------------------------------
# Triage ordering must not depend on a revenue figure.
# ---------------------------------------------------------------------------

def _verdict(cluster_id, severity, primary_delta):
    return ClusterVerdict(
        client_id="c1", client_name="Alpha", cluster_id=cluster_id, cluster_label=cluster_id,
        primary_competitor="Rival", triage_severity=severity,
        field=[{"competitor": "Rival", "delta_pp": primary_delta}],
    )


def test_triage_sorts_by_severity_then_movement():
    rows = TD.build_triage_digest([
        _verdict("small-critical", "CRITICAL", 2.0),
        _verdict("big-watch", "WATCH", 30.0),
        _verdict("big-critical", "CRITICAL", 25.0),
    ])
    assert [r["cluster_label"] for r in rows] == ["big-critical", "small-critical", "big-watch"]


def test_triage_carries_category_not_a_dollar():
    rows = TD.build_triage_digest([_verdict("clu1", "CRITICAL", 5.0)])
    assert rows[0]["revenue_category"] == "unavailable"
    assert "revenue_at_risk_usd" not in rows[0]
    assert "revenue_basis" not in rows[0]


# ---------------------------------------------------------------------------
# Outcome revenue measurement is scoped to mapped target pages, or it is unavailable.
# ---------------------------------------------------------------------------

def _om_config(**over):
    base = {"revenue_outcome_enabled": True, "geo_ga4_enabled": True}
    base.update(over)
    return types.SimpleNamespace(**base)


def test_outcome_revenue_is_unavailable_without_target_pages(monkeypatch):
    monkeypatch.setattr("scout.config.get_config", lambda: _om_config())
    value, category = OM._measure_revenue(object(), {"client_id": "c1", "target_pages": []})
    assert value is None
    assert category == "unavailable"


def test_outcome_revenue_is_unavailable_when_the_layer_is_off(monkeypatch):
    monkeypatch.setattr("scout.config.get_config",
                        lambda: _om_config(revenue_outcome_enabled=False))
    value, category = OM._measure_revenue(
        object(), {"client_id": "c1", "target_pages": ["https://client.com/procurement"]}
    )
    assert value is None
    assert category == "unavailable"


def _stub_ga4(monkeypatch, rows):
    monkeypatch.setattr("scout.config.get_config", lambda: _om_config())
    monkeypatch.setattr("scout.db.revenue_context.get_client_revenue_handles",
                        lambda sb, cid: {"ga4_property_id": "p1"})
    monkeypatch.setattr("scout.db.revenue_context.get_ga4_revenue", lambda sb, pid: rows)


def test_outcome_revenue_is_scoped_to_target_pages(monkeypatch):
    _stub_ga4(monkeypatch, [
        {"landing_page": "/procurement", "revenue": 750.0},
        {"landing_page": "/unrelated", "revenue": 9_000.0},
    ])
    value, category = OM._measure_revenue(
        object(), {"client_id": "c1", "target_pages": ["https://client.com/procurement"]}
    )
    assert value == 750.0          # the unrelated 9,000 is excluded
    assert category == "recorded"


def test_outcome_revenue_matches_ga4_absolute_urls_too(monkeypatch):
    """GA4 emits paths on some properties and absolute URLs on others.

    Both sides must be run through the same normalizer, or genuine revenue silently reports as
    `unavailable` — a failure that is safe but invisible."""
    _stub_ga4(monkeypatch, [
        {"landing_page": "https://client.com/procurement", "revenue": 750.0},
        {"landing_page": "https://client.com/unrelated", "revenue": 9_000.0},
    ])
    value, category = OM._measure_revenue(
        object(), {"client_id": "c1", "target_pages": ["https://client.com/procurement"]}
    )
    assert value == 750.0
    assert category == "recorded"


def test_outcome_revenue_matches_across_trailing_slash_and_query_string(monkeypatch):
    _stub_ga4(monkeypatch, [
        {"landing_page": "/procurement/?utm_source=ai", "revenue": 750.0},
    ])
    value, category = OM._measure_revenue(
        object(), {"client_id": "c1", "target_pages": ["https://client.com/procurement"]}
    )
    assert value == 750.0
    assert category == "recorded"


def test_ga4_sum_without_a_normalizer_compares_raw_values():
    """The default is identity, so a caller that normalizes only one side gets no match rather
    than a wrong total. Fails closed, which is why the bug was invisible until tested."""
    rows = [{"landing_page": "https://client.com/procurement", "revenue": 750.0}]
    assert R.actual_revenue_from_ga4(ga4_rows=rows, landing_pages={"/procurement"}) is None
