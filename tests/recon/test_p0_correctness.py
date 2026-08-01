# test_p0_correctness.py — regression tests for the P0 correctness fixes (cross-client SOV lookup,
# flat-baseline direction, blog_monitoring crash/attribution). Local-only (tests/ is untracked).
# Run with: python -m pytest tests/test_p0_correctness.py -q
import types
from datetime import date

import scout.db.sed_writer as SW
import scout.db.supabase_client as SC
import scout.nodes.blog_monitoring as BM
import scout.nodes.report_gen as RG
from scout.models.recommendation import Recommendation
from scout.models.sov import SOVTrackingRecord
from scout.nodes.sov_detection import classify_alert


# ---------------------------------------------------------------------------
# CRITICAL — recommendation_gen/report_gen client-SOV lookup is client-scoped.
# Two clients share cluster_id '3'; each must see ITS OWN client_sov, never the
# other's. Before the fix, a bare-cluster_id lookup shipped the first client's
# SOV ("Your AI search visibility here is X points") in the second client's summary.
# ---------------------------------------------------------------------------
def _rec(client_id, client_name):
    return Recommendation(
        investigation_id="tmp", client_id=client_id, client_name=client_name,
        competitor_name="OpenAI", cluster_id="3", cluster_label="Widgets",
        shift_type="gain", type="defensive", priority="standard",
        probable_cause="a real probable cause worth reporting",
        confidence="medium", gap_analysis="a gap analysis long enough to pass validation",
        action_bullets=["do a", "do b", "do c"], summary="s", slack_report="sr",
        timeline="2-4 weeks",
    )


def _sov(client_id, client_sov):
    return SOVTrackingRecord(
        client_id=client_id, cluster_id="3", competitor_name="OpenAI",
        week_date=date(2026, 7, 20), sov_score=30.0, client_sov_this_week=client_sov,
    )


def test_client_sov_lookup_is_client_scoped(monkeypatch):
    # Force the deterministic fallback template path (empty LLM response) so client_sov_now is rendered
    # into the summary text as "... is <n> points."
    monkeypatch.setattr(RG, "call_synthesis", lambda *a, **k: "")
    monkeypatch.setattr(RG, "get_config", lambda: types.SimpleNamespace(
        client_summary_min_chars=40,
        revenue_layer_enabled=False, asset_attribution_enabled=False,
        revenue_asset_surfacing_enabled=False))

    state = {
        "sync_date": "2026-07-20",
        "recommendations": [_rec("clientA", "Alpha"), _rec("clientB", "Beta")],
        "clients": [
            {"client_id": "clientA", "client_name": "Alpha"},
            {"client_id": "clientB", "client_name": "Beta"},
        ],
        # both share cluster '3'; distinct client SOV
        "sov_tracking_records": [_sov("clientA", 40.0), _sov("clientB", 10.0)],
    }
    out = RG.report_generation(state)
    summaries = {cs.client_id: cs.summary_text for cs in out["client_summaries"]}
    assert "40.0 points" in summaries["clientA"]
    assert "10.0 points" in summaries["clientB"]
    # the leak would put clientA's 40.0 into clientB's summary
    assert "40.0 points" not in summaries["clientB"]


# ---------------------------------------------------------------------------
# P0 — flat-baseline (std_dev=0) classification keeps DIRECTION: a competitor
# DROP off a flat history is a 'loss', not a 'gain'.
# ---------------------------------------------------------------------------
def _cfg():
    return types.SimpleNamespace(displacement_threshold_sd=1.5, detection_threshold_sd=1.0)


def test_flat_baseline_drop_is_loss_not_gain():
    # competitor_z is None (flat baseline), move exceeds the flat threshold, signed delta is negative
    alert_type, _priority, _ = classify_alert(
        competitor_z=None, client_z=None, history_weeks=4, sov_this_week=5.0,
        flat_exceeded=True, config=_cfg(), flat_delta=-5.0,
    )
    assert alert_type == "loss"


def test_flat_baseline_rise_is_gain():
    alert_type, _priority, _ = classify_alert(
        competitor_z=None, client_z=None, history_weeks=4, sov_this_week=15.0,
        flat_exceeded=True, config=_cfg(), flat_delta=5.0,
    )
    assert alert_type == "gain"


# ---------------------------------------------------------------------------
# P0 — one malformed LLM classification item must not crash the weekly run, and an
# unmatched cluster must NOT fall back to client_clusters[0] (fabricated attribution).
# ---------------------------------------------------------------------------
def test_blog_monitoring_survives_malformed_and_does_not_fabricate(monkeypatch):
    monkeypatch.setattr(BM, "get_config", lambda: types.SimpleNamespace(
        blog_monitoring_enabled=True, max_blog_triggers_per_cycle=0))
    monkeypatch.setattr(BM, "_get_db_entries",
                        lambda domain, since: [{"url": "https://openai.com/x", "title": "X", "_source": "feed"}])
    monkeypatch.setattr(BM, "_classify_with_claude", lambda *a, **k: [
        {"no_url": True},                                  # malformed: missing 'url' -> must be skipped, not crash
        {"url": "https://openai.com/x", "content_type": "blog_post", "topic": "t",
         "relevance_to_clusters": [{"cluster_id": "999", "relevance": "direct"}]},  # cluster no client tracks
    ])

    state = {
        "sync_date": "2026-07-20",
        "clients": [
            {"client_id": "clientB", "client_name": "Beta", "clusters": [
                {"cluster_id": "3", "cluster_label": "L3",
                 "competitors": [{"competitor_name": "OpenAI", "competitor_domain": "openai.com"}]}]},
        ],
    }
    out = BM.blog_monitoring(state)   # must not raise
    # the valid-url post is recorded, the malformed one skipped
    assert "openai.com" in out["blog_detections"]
    assert len(out["blog_detections"]["openai.com"].new_blog_posts) == 1
    # cluster '999' matches no client_cluster -> NO fabricated trigger against client[0]
    assert out["blog_investigation_triggers"] == []


# ---------------------------------------------------------------------------
# P0 — a persistence failure is REPORTED (not swallowed), so run.py can mark the
# cycle 'failed' instead of a silent 'completed' that blocks the healing retry.
# ---------------------------------------------------------------------------
class _NoopSB:
    def table(self, *_a, **_k):
        return self

    def upsert(self, *_a, **_k):
        return self

    def insert(self, *_a, **_k):
        return self

    def update(self, *_a, **_k):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        return types.SimpleNamespace(data=[])


def test_persist_failure_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(SC, "get_sed_client", lambda: _NoopSB())

    def boom(*_a, **_k):
        raise RuntimeError("simulated recommendations write failure")

    monkeypatch.setattr(SW, "_write_recommendations", boom)

    state = {
        "run_id": "run1", "sync_date": "2026-07-20",
        "clients": [{"client_id": "clientA", "client_name": "Alpha"}],
        "recommendations": [], "internal_reports": [], "client_summaries": [],
        "investigation_triggers": [], "sov_tracking_records": [],
        "website_changes": {}, "third_party_signals": {}, "ai_citation_changes": {},
        "blog_detections": {}, "cluster_verdicts": [],
    }
    result = SW.write_outputs_to_sed(state)
    assert "failures" in result and "counts" in result
    assert "recommendations" in result["failures"]
    assert result["counts"]["recommendations"] == 0
