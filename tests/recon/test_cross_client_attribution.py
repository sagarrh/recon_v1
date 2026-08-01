# test_cross_client_attribution.py — regression tests for the cross-client attribution fixes.
# Covers the case two clients share a GEO cluster_id ('3') so a bare cluster_id / cluster_label / client[0]
# attribution would leak one client's data into another's. Local-only (tests/ is untracked).
# Run with: python -m pytest tests/test_cross_client_attribution.py -q
import types

import scout.nodes.slack_delivery as S
from scout.db import reader as R
from scout.db import sed_mapping as m
from scout.db import sed_writer as W
from scout.models.recommendation import ClientSummary, InternalReport, Recommendation
from scout.models.sov import InvestigationTrigger


# ---------------------------------------------------------------------------
# Minimal in-memory Supabase double
# ---------------------------------------------------------------------------
class FakeResp:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, name, store):
        self.name = name
        self.store = store
        self._rows = None
        self._op = None

    def insert(self, rows):
        self._op = "insert"
        self._rows = rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows, on_conflict=None):
        self._op = "upsert"
        self._rows = rows if isinstance(rows, list) else [rows]
        return self

    def select(self, *a, **k):
        self._op = "select"
        return self

    def update(self, *a, **k):
        self._op = "update"
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self._op in ("insert", "upsert"):
            base = self.store.setdefault(self.name, [])
            out = []
            for r in self._rows:
                row = dict(r)
                row.setdefault("id", f"{self.name}-{len(base) + len(out)}")
                out.append(row)
            base.extend(out)
            return FakeResp(out)
        return FakeResp(list(self.store.get(self.name, [])))


class FakeSB:
    def __init__(self, seed=None):
        self.store = dict(seed or {})

    def table(self, name):
        return FakeQuery(name, self.store)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------
def _rec(client_id, client_name):
    return Recommendation(
        investigation_id="tmp", client_id=client_id, client_name=client_name,
        competitor_name="OpenAI", cluster_id="3", cluster_label="Widgets",
        shift_type="gain", type="offensive", priority="standard",
        probable_cause="a real probable cause", confidence="medium",
        gap_analysis="a gap analysis long enough to pass validation",
        action_bullets=["a", "b", "c"], summary="s", slack_report="sr",
    )


def _trigger(client_id, client_name):
    return InvestigationTrigger(
        client_id=client_id, client_name=client_name,
        competitor_name="OpenAI", competitor_domain="openai.com",
        cluster_id="3", cluster_label="Widgets",
        shift_type="gain", shift_magnitude=5.0,
        investigation_priority="standard", triage_reason="test",
    )


def _two_client_state():
    recA, recB = _rec("clientA", "Alpha"), _rec("clientB", "Beta")
    irA = InternalReport(client_id="clientA", client_name="Alpha", competitor_name="OpenAI",
                         cluster_id="3", cluster_label="Widgets", priority="standard", report_text="A" * 210)
    irB = InternalReport(client_id="clientB", client_name="Beta", competitor_name="OpenAI",
                         cluster_id="3", cluster_label="Widgets", priority="standard", report_text="B" * 210)
    csA = ClientSummary(client_id="clientA", client_name="Alpha", cluster_id="3", cluster_label="Widgets", summary_text="SUMMARY_A")
    csB = ClientSummary(client_id="clientB", client_name="Beta", cluster_id="3", cluster_label="Widgets", summary_text="SUMMARY_B")
    return {
        "run_id": "run1", "sync_date": "2026-07-20",
        # both clients share cluster_id "3" with the same competitor "OpenAI"
        "website_changes": {"clientA::OpenAI::3": {"changed": 1}, "clientB::OpenAI::3": {"changed": 1}},
        "third_party_signals": {}, "ai_citation_changes": {},
        "investigation_triggers": [_trigger("clientA", "Alpha"), _trigger("clientB", "Beta")],
        "recommendations": [recA, recB],
        "internal_reports": [irA, irB],
        "client_summaries": [csA, csB],
        "clients": [
            {"client_id": "clientA", "client_name": "Alpha"},
            {"client_id": "clientB", "client_name": "Beta"},
        ],
    }


# ---------------------------------------------------------------------------
# Fix 1 — models carry client_id / cluster_id
# ---------------------------------------------------------------------------
def test_artifacts_carry_client_scope():
    r = _rec("cid", "Name")
    assert r.client_id == "cid"
    ir = InternalReport(client_id="cid", client_name="n", competitor_name="c",
                        cluster_id="7", cluster_label="l", priority="standard", report_text="x" * 210)
    cs = ClientSummary(client_id="cid", client_name="n", cluster_id="7", cluster_label="l", summary_text="s")
    assert (ir.client_id, ir.cluster_id) == ("cid", "7")
    assert (cs.client_id, cs.cluster_id) == ("cid", "7")


# ---------------------------------------------------------------------------
# Fix 3 — sed_writer id-maps are client-scoped (no cross-client FK collision)
# ---------------------------------------------------------------------------
def test_investigation_and_rec_maps_do_not_collide_across_clients():
    state = _two_client_state()
    sb = FakeSB()
    client_ids = W._client_id_lookup(state)
    trigger_meta = W._trigger_lookup(state)

    inv_map = W._write_investigations(sb, state, {}, trigger_meta)
    assert set(inv_map.keys()) == {("clientA", "OpenAI", "3"), ("clientB", "OpenAI", "3")}
    assert inv_map[("clientA", "OpenAI", "3")] != inv_map[("clientB", "OpenAI", "3")]

    rec_map = W._write_recommendations(sb, state, client_ids, inv_map)
    assert set(rec_map.keys()) == {("clientA", "OpenAI", "3"), ("clientB", "OpenAI", "3")}

    # each recommendation row got ITS OWN investigation_id FK (not the other client's)
    rec_rows = {r["client_id"]: r for r in sb.store[m.SCOUT_RECOMMENDATIONS_TABLE]}
    assert rec_rows["clientA"]["investigation_id"] == inv_map[("clientA", "OpenAI", "3")]
    assert rec_rows["clientB"]["investigation_id"] == inv_map[("clientB", "OpenAI", "3")]
    assert rec_rows["clientA"]["investigation_id"] != rec_rows["clientB"]["investigation_id"]

    # reports attach each client's own recommendation_id
    W._write_reports(sb, state, client_ids, rec_map)
    rep_rows = {r["client_id"]: r for r in sb.store[m.SCOUT_REPORTS_TABLE]}
    assert rep_rows["clientA"]["recommendation_id"] == rec_map[("clientA", "OpenAI", "3")]
    assert rep_rows["clientB"]["recommendation_id"] == rec_map[("clientB", "OpenAI", "3")]
    assert rep_rows["clientA"]["recommendation_id"] != rep_rows["clientB"]["recommendation_id"]


# ---------------------------------------------------------------------------
# Fix 4 — Slack client-summary join is client-scoped (not by shared cluster_label)
# ---------------------------------------------------------------------------
def test_slack_join_attaches_each_clients_own_summary(monkeypatch):
    captured = []
    monkeypatch.setattr(S, "get_config", lambda: types.SimpleNamespace(
        slack_enabled=True, quarantine_rate_alert_threshold=1.0))
    monkeypatch.setattr(S, "post_intel", lambda text, blocks: True)
    monkeypatch.setattr(S, "post_alert", lambda *a, **k: True)
    monkeypatch.setattr(S, "_build_recommendation_blocks",
                        lambda rec, summary_text, run_id, sync_date: captured.append((rec.client_id, summary_text)) or [])
    monkeypatch.setattr(S, "_build_quarantine_block", lambda *a, **k: None)
    monkeypatch.setattr(S, "_build_cycle_issues_block", lambda *a, **k: None)

    state = _two_client_state()
    state["cycle_summary"] = None
    state["triage_digest"] = []
    S.slack_delivery(state)

    got = dict(captured)
    # both clients share cluster_label "Widgets"; each must still get its own summary
    assert got["clientA"] == "SUMMARY_A"
    assert got["clientB"] == "SUMMARY_B"


# ---------------------------------------------------------------------------
# Fix 5 — blog detections attributed to the classified client, not client[0]
# ---------------------------------------------------------------------------
def test_blog_detection_attributed_to_correct_client():
    state = {
        "run_id": "run1", "sync_date": "2026-07-20",
        "clients": [
            # clientA is FIRST (would be the old fallback) but does NOT track openai.com
            {"client_id": "clientA", "client_name": "Alpha", "clusters": [
                {"cluster_id": "1", "cluster_label": "L1",
                 "competitors": [{"competitor_name": "Acme", "competitor_domain": "acme.com"}]}]},
            {"client_id": "clientB", "client_name": "Beta", "clusters": [
                {"cluster_id": "3", "cluster_label": "L3",
                 "competitors": [{"competitor_name": "OpenAI", "competitor_domain": "openai.com"}]}]},
        ],
        "blog_detections": {
            "openai.com": {
                "competitor_domain": "openai.com", "detection_method": "feed",
                "new_blog_posts": [{
                    "url": "https://openai.com/blog/x", "title": "X",
                    "relevance_to_clusters": [{"cluster_id": "3", "relevance": "direct", "reason": "r"}],
                }],
            }
        },
    }
    sb = FakeSB()
    client_ids = W._client_id_lookup(state)   # fallback (first) would be clientA
    n = W._write_blog_detections(sb, state, client_ids)
    assert n == 1
    row = sb.store[m.SCOUT_BLOG_DETECTIONS_TABLE][0]
    assert row["client_id"] == "clientB"   # the client whose cluster the post was classified against


# ---------------------------------------------------------------------------
# Fix 6 — reader emits the __client__ SOV series on the primary sov_weekly path
# ---------------------------------------------------------------------------
def test_reader_emits_client_sov_on_sov_weekly_path(monkeypatch):
    client_rows = [{
        m.CLIENT_COLS["client_id"]: "clientA",
        m.CLIENT_COLS["client_name"]: "Alpha",
        m.CLIENT_COLS["company_domain"]: "alpha.com",
        m.CLIENT_COLS["company_website"]: "https://alpha.com",
        m.CLIENT_COLS["competitors"]: "[]",
        m.CLIENT_COLS["competitors_url"]: "[]",
    }]
    sov_rows = [{
        m.SOV_COLS["client_id"]: "clientA",
        m.SOV_COLS["cluster_id"]: "3",
        m.SOV_COLS["cluster_name"]: "Widgets",
        m.SOV_COLS["top_companies"]: [{"name": "Alpha", "sov_score": 40.0},
                                      {"name": "OpenAI", "sov_score": 30.0}],
        m.SOV_COLS["week_date"]: "2026-07-13",
        m.SOV_COLS["created_at"]: "2026-07-13T00:00:00Z",
    }]
    sb = FakeSB(seed={m.CLIENT_TABLE: client_rows, m.SOV_TABLE: sov_rows})

    monkeypatch.setattr("scout.db.supabase_client.get_sed_client", lambda: sb)
    monkeypatch.setattr(R, "_fetch_ai_clusters_for_client", lambda _sb, _cid: [])
    monkeypatch.setattr(R, "get_config", lambda: types.SimpleNamespace(
        ignore_staleness=True, sov_critical_streams=[],
        geo_report_data_sov_enabled=False, geo_cluster_registry_enabled=False))

    bundle = R._load_from_supabase_direct(None)
    clusters = bundle["clients"][0]["clusters"]
    cluster3 = next(c for c in clusters if c["cluster_id"] == "3")
    # The client's own SOV (40.0) is surfaced instead of the silent 0.0 default
    assert cluster3["client_sov_this_week"] == 40.0
