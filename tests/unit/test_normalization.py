from __future__ import annotations

from conftest import MAIN_URL, NEW_URL

from ai_visibility.analysis.citations import citation_deltas
from ai_visibility.normalization.models import RawMonitoringRun
from ai_visibility.normalization.runs import normalize_run
from ai_visibility.utils.text import literal_mentions
from ai_visibility.utils.urls import normalize_url


def test_aprio_literal_metric_and_citation_counts(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
) -> None:
    previous_raw, current_raw = raw_aprio_pair
    companies = {
        "Aprio",
        "Cherry Bekaert",
        "CohnReznick",
        "BDO",
        "Baker Tilly",
        "Bennett Thrasher",
    }
    previous = normalize_run(previous_raw, companies)
    current = normalize_run(current_raw, companies)
    previous_metric = previous.company_metrics["aprio"]
    current_metric = current.company_metrics["aprio"]
    assert previous_metric.literal_answer_count == 0
    assert len(previous.answers) == 20
    assert current_metric.literal_answer_count == 14
    assert current_metric.literal_answer_numbers == list(range(8, 22))
    assert current_metric.literal_visibility == 14 / 21
    assert current_metric.upstream_count == 10
    assert current_metric.metric_difference == 4
    assert "company_metric_mismatch" in current.data_quality_flags
    assert "citation_positions_unavailable" in current.data_quality_flags
    assert previous.monitor_query_key == current.monitor_query_key

    deltas = {item["url"]: item for item in citation_deltas(previous, current, "Aprio")}
    main = deltas[normalize_url(MAIN_URL)]
    assert main["previous_raw_occurrences"] == 24
    assert main["current_raw_occurrences"] == 87
    assert main["previous_answer_coverage"] == 17
    assert main["current_answer_coverage"] == 21
    assert main["previous_company_cooccurrence"] == 0
    assert main["current_company_cooccurrence"] == 14
    assert abs(main["current_association_lift"]) < 1e-12
    new = deltas[normalize_url(NEW_URL)]
    assert new["status"] == "new"
    assert new["current_answer_coverage"] == 1


def test_url_normalization_preserves_meaningful_parameters() -> None:
    first = normalize_url("https://YouTube.com/watch?v=abc&utm_source=x#part")
    second = normalize_url("https://youtube.com/watch?v=def")
    assert first == "https://youtube.com/watch?v=abc"
    assert second == "https://youtube.com/watch?v=def"
    assert first != second
    assert normalize_url("https://example.com/page?id=1&utm_campaign=x") == (
        "https://example.com/page?id=1"
    )
    assert normalize_url("https://example.com/x?step=1&step=2") != normalize_url(
        "https://example.com/x?step=2&step=1"
    )


def test_literal_matching_protects_substrings() -> None:
    assert literal_mentions("Aprio is listed.", ["Aprio"])[0] == 1
    assert literal_mentions("The capriotic example is unrelated.", ["Aprio"])[0] == 0


def test_overlapping_aliases_are_counted_once() -> None:
    assert literal_mentions("BDO USA is listed.", ["BDO USA", "BDO"]) == (1, 0)
