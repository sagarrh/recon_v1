from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from conftest import CLIENT_ID, MAIN_URL, NEW_URL

from ai_visibility.config.settings import Settings
from ai_visibility.normalization.models import RawMonitoringRun
from ai_visibility.normalization.runs import normalize_run
from ai_visibility.reports.builder import build_report
from ai_visibility.reports.markdown import render_markdown
from ai_visibility.reports.persistence import write_report_files
from ai_visibility.utils.urls import normalize_url


def test_aprio_report_acceptance(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
) -> None:
    companies = {
        "Aprio",
        "Cherry Bekaert",
        "CohnReznick",
        "BDO",
        "Baker Tilly",
        "Bennett Thrasher",
        "Eubanks Accounting & Advisory",
        "HKA",
    }
    runs = [normalize_run(raw, companies) for raw in raw_aprio_pair]
    report = build_report(runs, "Aprio", client_id=str(CLIENT_ID))
    payload = report.model_dump(mode="json")
    assert payload["analysis_period"]["valid_run_count"] == 2
    assert payload["signals"]
    signal = payload["signals"][0]
    assert signal["observed"]["previous_literal_visibility"] == 0
    assert signal["observed"]["current_literal_visibility"] == 14 / 21
    assert signal["observed"]["metric_difference"] == 4
    assert signal["primary_hypothesis"]["type"] == "recommendation_pattern_shift"
    assert signal["confidence"] == "medium"
    assert signal["single_page_causation_confidence"] == "low"
    sources = {item["url"]: item for item in signal["evidence"]["sources"]}
    main = next(value for key, value in sources.items() if MAIN_URL.rstrip("/") in key)
    new = next(value for key, value in sources.items() if NEW_URL.rstrip("/") in key)
    assert main["relationship"] == "supporting_or_topic_context"
    assert main["attribution"]["confidence"] == "low"
    assert new["relationship"] == "localized_new_owned_source"
    assert new["attribution"]["confidence"] == "low"
    assert "company_metric_mismatch" in payload["data_quality_flags"]
    markdown = render_markdown(payload)
    assert "Recommendation Pattern Shift" in markdown
    assert "proven direct driver" not in markdown.casefold()

    fixture_root = Path(__file__).resolve().parents[2] / "fixtures"
    expected_signal = json.loads(
        (fixture_root / "expected_aprio_signal.json").read_text(encoding="utf-8")
    )
    assert signal["signal_type"] == expected_signal["signal_type"]
    assert signal["primary_hypothesis"]["type"] == expected_signal["primary_hypothesis"]["type"]
    assert signal["confidence"] == expected_signal["primary_hypothesis"]["confidence"]
    assert (
        signal["single_page_causation_confidence"]
        == expected_signal["single_page_causation_confidence"]
    )
    assert (
        signal["new_owned_article_overall_contribution"]
        == expected_signal["new_owned_article_overall_contribution"]
    )
    assert set(expected_signal["data_quality_flags"]).issubset(payload["data_quality_flags"])

    expected_competitors = json.loads(
        (fixture_root / "aprio_competitor_delta_excerpt.json").read_text(encoding="utf-8")
    )
    competitors = {item["company"]: item for item in payload["competitor_intelligence"]}
    for expected in expected_competitors:
        if expected["company"] == "Aprio":
            continue
        actual = competitors[expected["company"]]
        assert actual["status"] == expected["status"]
        assert actual["previous_mentions"] == expected["previous_mentions"]
        assert actual["current_mentions"] == expected["current_mentions"]
        assert abs(actual["visibility_delta"] - expected["visibility_delta"]) < 1e-9


def test_full_history_excludes_invalid_runs_and_isolates_providers(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
) -> None:
    previous, current = raw_aprio_pair
    invalid = current.model_copy(
        update={
            "id": uuid4(),
            "created_at": current.created_at + timedelta(days=1),
            "answers_list": [],
            "citations_list": [],
        }
    )
    other_provider = current.model_copy(
        update={
            "id": uuid4(),
            "created_at": current.created_at + timedelta(days=2),
            "request_payload": {
                **current.request_payload,
                "service": "openai",
            },
        }
    )
    companies = {"Aprio", "BDO", "Cherry Bekaert"}
    runs = [normalize_run(raw, companies) for raw in (previous, current, invalid, other_provider)]
    report = build_report(runs, "Aprio", client_id=str(CLIENT_ID)).model_dump(mode="json")
    assert report["analysis_period"]["valid_run_count"] == 3
    assert report["analysis_period"]["invalid_run_count"] == 1
    assert report["analysis_period"]["provider_count"] == 2
    assert {item["provider"] for item in report["query_intelligence"]} == {
        "gemini",
        "openai",
    }


def test_provider_latest_visibility_aggregates_latest_query_state(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
) -> None:
    previous, current = raw_aprio_pair
    second_query = current.model_copy(
        update={
            "id": uuid4(),
            "created_at": current.created_at + timedelta(days=1),
            "request_payload": {
                **current.request_payload,
                "base_query": "A different monitored question",
            },
            "answers_list": ["No client mention."] * 21,
            "companies_data": {"Aprio": {"count": 0, "visibility": 0.0}},
        }
    )
    runs = [normalize_run(raw, {"Aprio"}) for raw in (previous, current, second_query)]
    report = build_report(runs, "Aprio", client_id=str(CLIENT_ID)).model_dump(mode="json")
    provider = report["provider_intelligence"][0]
    assert provider["query_count"] == 2
    assert provider["latest_literal_answer_count"] == 14
    assert provider["latest_total_answer_count"] == 42
    assert provider["latest_visibility"] == 14 / 42


def test_temporally_aligned_page_diff_updates_attribution(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
) -> None:
    previous, current = raw_aprio_pair
    runs = [normalize_run(raw, {"Aprio"}) for raw in raw_aprio_pair]
    page_evidence = [
        {
            "normalized_url": normalize_url(MAIN_URL),
            "mention_count": 3,
            "mentioned_in_title": True,
            "mentioned_in_heading": True,
            "links_to_official_domain": True,
            "publisher_is_company": True,
            "extraction_quality": {"quality": "high", "usable": True},
            "change_summary": {
                "extraction_quality_usable": True,
                "company_mention_changes": [
                    {
                        "company": "Aprio",
                        "previous_count": 0,
                        "current_count": 3,
                        "delta": 3,
                    }
                ],
            },
            "previous_snapshot_at": (previous.created_at - timedelta(days=1)).isoformat(),
            "current_snapshot_at": (current.created_at + timedelta(days=1)).isoformat(),
        }
    ]
    report = build_report(
        runs,
        "Aprio",
        client_id=str(CLIENT_ID),
        page_evidence=page_evidence,
    ).model_dump(mode="json")
    signal = report["signals"][0]
    main_source = next(
        source
        for source in signal["evidence"]["sources"]
        if source["url"] == normalize_url(MAIN_URL)
    )
    assert main_source["page_diff_strengthened"] is True
    assert main_source["snapshot_temporally_aligned"] is True
    assert signal["primary_hypothesis"]["type"] == "direct_page_content_change"


def test_low_quality_page_extraction_cannot_support_direct_attribution(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
) -> None:
    previous, current = raw_aprio_pair
    runs = [normalize_run(raw, {"Aprio"}) for raw in raw_aprio_pair]
    page_evidence = [
        {
            "normalized_url": normalize_url(MAIN_URL),
            "mention_count": 0,
            "extraction_quality": {"quality": "blocked", "usable": False},
            "change_summary": {
                "extraction_quality_usable": False,
                "company_mention_changes": [
                    {
                        "company": "Aprio",
                        "previous_count": 0,
                        "current_count": 3,
                        "delta": 3,
                    }
                ],
            },
            "previous_snapshot_at": (previous.created_at - timedelta(days=1)).isoformat(),
            "current_snapshot_at": (current.created_at + timedelta(days=1)).isoformat(),
        }
    ]
    report = build_report(
        runs,
        "Aprio",
        client_id=str(CLIENT_ID),
        page_evidence=page_evidence,
    ).model_dump(mode="json")
    signal = report["signals"][0]
    main_source = next(
        source
        for source in signal["evidence"]["sources"]
        if source["url"] == normalize_url(MAIN_URL)
    )
    assert main_source["page_diff_strengthened"] is False
    assert signal["primary_hypothesis"]["type"] != "direct_page_content_change"
    assert "page_does_not_mention_company" not in {
        component["component"] for component in main_source["attribution"]["components"]
    }


def test_report_files_are_idempotent(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
    tmp_path: Path,
) -> None:
    companies = {"Aprio", "BDO", "Cherry Bekaert"}
    runs = [normalize_run(raw, companies) for raw in raw_aprio_pair]
    payload = build_report(runs, "Aprio", client_id=str(CLIENT_ID)).model_dump(mode="json")
    settings = Settings(report_output_dir=tmp_path)
    first = write_report_files(settings, "Aprio", payload)
    second = write_report_files(settings, "Aprio", payload)
    assert first == second
    assert json.loads(first[0].read_text(encoding="utf-8")) == payload
    assert first[1].read_text(encoding="utf-8").startswith("# Company Intelligence Report: Aprio")
