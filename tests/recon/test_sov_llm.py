# test_sov_llm.py — unit tests for the LLM text-share SOV compute (scout/db/sov_llm.py), stubbed LLM.
# Local-only (tests/ is untracked); run with: python -m pytest tests/test_sov_llm.py -q
import json

import pytest

# This ignored local suite targets an experimental module that is absent from
# both the clean imported repository and its original checkout. Keep the tests
# tracked as historical specification without inventing a dead LLM-counting
# path; the integrated implementation uses deterministic citation metrics.
sov_llm = pytest.importorskip(
    "scout.db.sov_llm",
    reason="legacy experimental sov_llm module is not part of Recon V1",
)


class FakeConfig:
    sov_llm_max_answers_per_bucket = 40
    sov_llm_model = ""
    llm_max_concurrency = 2


def _bucket(queries):
    return {"cluster_name": "CNC Machining", "queries": queries, "source": "ai_responses"}


def _stub_llm(monkeypatch, ner_by_answer, filter_result=None, filter_raises=False):
    """Route sov_llm.call_extraction to canned outputs: NER keyed by passage content, one filter response."""
    def fake_call(system_prompt, user_message, expect_json=True, node_name="", trigger_key="", model=None):
        if node_name == "sov_ner":
            for needle, reply in ner_by_answer.items():
                if needle in user_message:
                    return reply
            return "[]"
        if node_name == "sov_filter":
            if filter_raises:
                raise ValueError("boom")
            if filter_result is not None:
                return json.dumps(filter_result)
            # default: keep everything listed as "- name" candidates
            kept = [line[2:] for line in user_message.splitlines() if line.startswith("- ")]
            return json.dumps(kept)
        raise AssertionError(f"unexpected node_name {node_name}")
    monkeypatch.setattr(sov_llm, "call_extraction", fake_call)
    monkeypatch.setattr(sov_llm, "load_prompt", lambda name: f"<{name}>")


def _scores(entries):
    return {e["entity_name"]: round(e["sov_score"], 2) for e in entries}


def test_spec_worked_example(monkeypatch):
    """sov_method.md worked example: 8 vs 4 words -> 66.67 / 33.33 pp; client renamed to __client__."""
    _stub_llm(monkeypatch, {
        "Acme AI is strong": '[{"company_name": "Acme AI", "text_length": "8"},'
                             ' {"company_name": "Rankly", "text_length": "4"}]',
    })
    bucket = _bucket([{"platform": "chatgpt", "query": "q1",
                       "answers": ["Acme AI is strong for AI visibility monitoring. Rankly is also known."]}])
    ref = {"name": "Acme AI", "description": "AI visibility platform", "metadata": "GEO"}
    entries, stats = sov_llm._compute_bucket(bucket, ref, "Acme AI", FakeConfig(), "t")
    assert _scores(entries) == {"__client__": 66.67, "Rankly": 33.33}
    assert stats == {"query_count": 1, "answer_count": 1}


def test_cluster_averaging_matches_spec(monkeypatch):
    """Per-query visibilities 0.20 / 0.50 / 0.00 average to 23.33 pp (spec cluster example)."""
    _stub_llm(monkeypatch, {
        "ANSWER_ONE": '[{"company_name": "Acme", "text_length": 20}, {"company_name": "Beta", "text_length": 80}]',
        "ANSWER_TWO": '[{"company_name": "Acme", "text_length": 50}, {"company_name": "Beta", "text_length": 50}]',
        "ANSWER_THREE": '[{"company_name": "Beta", "text_length": 100}]',
    })
    bucket = _bucket([
        {"platform": "p", "query": "q1", "answers": ["ANSWER_ONE"]},
        {"platform": "p", "query": "q2", "answers": ["ANSWER_TWO"]},
        {"platform": "p", "query": "q3", "answers": ["ANSWER_THREE"]},
    ])
    ref = {"name": "Nobody Inc", "description": "", "metadata": ""}
    entries, stats = sov_llm._compute_bucket(bucket, ref, "Nobody Inc", FakeConfig(), "t")
    scores = _scores(entries)
    assert scores["Acme"] == 23.33
    assert scores["Beta"] == round((0.8 + 0.5 + 1.0) / 3 * 100, 2)
    assert scores["__client__"] == 0.0   # unmatched client still emits the zero sentinel row
    assert stats["query_count"] == 3


def test_canonical_name_merging(monkeypatch):
    """'OpenAI' and 'openai' merge under first-seen casing; word counts sum."""
    _stub_llm(monkeypatch, {
        "ANSWER_ONE": '[{"company_name": "OpenAI", "text_length": 10}]',
        "ANSWER_TWO": '[{"company_name": "openai", "text_length": 30}]',
    })
    bucket = _bucket([
        {"platform": "p", "query": "q1", "answers": ["ANSWER_ONE"]},
        {"platform": "p", "query": "q2", "answers": ["ANSWER_TWO"]},
    ])
    ref = {"name": "Client Co", "description": "", "metadata": ""}
    entries, _ = sov_llm._compute_bucket(bucket, ref, "Client Co", FakeConfig(), "t")
    names = {e["entity_name"] for e in entries}
    assert "OpenAI" in names and "openai" not in names
    openai_entry = next(e for e in entries if e["entity_name"] == "OpenAI")
    assert openai_entry["sov_score"] == 100.0   # sole company in both queries
    assert openai_entry["word_count"] == 40


def test_filter_drop_recomputes_denominator(monkeypatch):
    """Filter dropping 'Irrelevant' removes its words from every query's total."""
    _stub_llm(monkeypatch, {
        "ANSWER_ONE": '[{"company_name": "Acme", "text_length": 30},'
                      ' {"company_name": "Irrelevant", "text_length": 70}]',
    }, filter_result=["Acme"])
    bucket = _bucket([{"platform": "p", "query": "q1", "answers": ["ANSWER_ONE"]}])
    ref = {"name": "Acme", "description": "", "metadata": ""}
    entries, _ = sov_llm._compute_bucket(bucket, ref, "Acme", FakeConfig(), "t")
    scores = _scores(entries)
    assert scores == {"__client__": 100.0}   # Acme==client; Irrelevant filtered out entirely


def test_filter_failure_keeps_all(monkeypatch):
    """A crashing filter call keeps every extracted company (spec's when-in-doubt-keep)."""
    _stub_llm(monkeypatch, {
        "ANSWER_ONE": '[{"company_name": "Acme", "text_length": 50},'
                      ' {"company_name": "Beta", "text_length": 50}]',
    }, filter_raises=True)
    bucket = _bucket([{"platform": "p", "query": "q1", "answers": ["ANSWER_ONE"]}])
    ref = {"name": "Nobody Inc", "description": "", "metadata": ""}
    entries, _ = sov_llm._compute_bucket(bucket, ref, "Nobody Inc", FakeConfig(), "t")
    scores = _scores(entries)
    assert scores["Acme"] == 50.0 and scores["Beta"] == 50.0


def test_garbage_text_length_clamps_to_zero(monkeypatch):
    _stub_llm(monkeypatch, {
        "ANSWER_ONE": '[{"company_name": "Acme", "text_length": "abc"},'
                      ' {"company_name": "Beta", "text_length": -5},'
                      ' {"company_name": "Gamma", "text_length": "12"}]',
    })
    bucket = _bucket([{"platform": "p", "query": "q1", "answers": ["ANSWER_ONE"]}])
    ref = {"name": "Nobody Inc", "description": "", "metadata": ""}
    entries, _ = sov_llm._compute_bucket(bucket, ref, "Nobody Inc", FakeConfig(), "t")
    scores = _scores(entries)
    assert scores["Gamma"] == 100.0
    assert scores["Acme"] == 0.0 and scores["Beta"] == 0.0


def test_all_ner_failed_returns_none(monkeypatch):
    def fake_call(system_prompt, user_message, expect_json=True, node_name="", trigger_key="", model=None):
        raise RuntimeError("api down")
    monkeypatch.setattr(sov_llm, "call_extraction", fake_call)
    monkeypatch.setattr(sov_llm, "load_prompt", lambda name: "<p>")
    bucket = _bucket([{"platform": "p", "query": "q1", "answers": ["whatever"]}])
    ref = {"name": "X", "description": "", "metadata": ""}
    assert sov_llm._compute_bucket(bucket, ref, "X", FakeConfig(), "t") is None


def test_weeks_to_compute_skips_persisted_recomputes_current():
    buckets = {("c1", "2026-06-22"): {}, ("c1", "2026-06-29"): {}, ("c1", "2026-07-06"): {}}
    persisted = {("c1", "2026-06-22"): {}, ("c1", "2026-07-06"): {}}
    todo = sov_llm._weeks_to_compute(buckets, persisted, current_week="2026-07-06")
    assert todo == [("c1", "2026-06-29"), ("c1", "2026-07-06")]   # missing week + current week always


def test_extract_json_array_variants():
    assert sov_llm._extract_json_array('[{"a": 1}]') == [{"a": 1}]
    assert sov_llm._extract_json_array('```json\n[1, 2]\n```') == [1, 2]
    assert sov_llm._extract_json_array('Here you go:\n[{"company_name": "X"}] hope that helps') == [{"company_name": "X"}]
    assert sov_llm._extract_json_array('{"not": "a list"}') is None
    assert sov_llm._extract_json_array("total garbage") is None
    assert sov_llm._extract_json_array([1]) == [1]


def test_answer_cap_truncates_deterministically(monkeypatch):
    calls = []

    def fake_call(system_prompt, user_message, expect_json=True, node_name="", trigger_key="", model=None):
        if node_name == "sov_ner":
            calls.append(user_message)
            return '[{"company_name": "Acme", "text_length": 5}]'
        return '["Acme"]'
    monkeypatch.setattr(sov_llm, "call_extraction", fake_call)
    monkeypatch.setattr(sov_llm, "load_prompt", lambda name: "<p>")

    cfg = FakeConfig()
    cfg.sov_llm_max_answers_per_bucket = 2
    bucket = _bucket([
        {"platform": "a", "query": "q1", "answers": ["A1", "A2"]},
        {"platform": "b", "query": "q2", "answers": ["B1"]},
    ])
    ref = {"name": "Acme", "description": "", "metadata": ""}
    entries, stats = sov_llm._compute_bucket(bucket, ref, "Acme", cfg, "t")
    assert len(calls) == 2                      # capped: only q1's two answers processed
    assert stats["query_count"] == 1            # q2 never extracted -> excluded from the denominator
    assert _scores(entries) == {"__client__": 100.0}


def test_client_similarity_threshold():
    assert sov_llm._similarity("Acme AI", "Acme AI") == 100.0
    assert sov_llm._similarity("ACME ai ", "acme ai") == 100.0
    assert sov_llm._similarity("Completely Different", "Acme AI") < 80.0
