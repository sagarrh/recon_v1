from conftest import CLIENT_ID

from ai_visibility.normalization.models import RawMonitoringRun
from ai_visibility.normalization.runs import normalize_run
from ai_visibility.reports.builder import build_report
from aivc.producers.citation_bundle import build_citation_bundle


def test_citation_bundle_exports_all_adjacent_and_competitor_changes(
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
    report = build_report(runs, "Aprio", client_id=str(CLIENT_ID)).model_dump(mode="json")

    bundle = build_citation_bundle(
        runs,
        report,
        producer_version="0.1.0",
        producer_run_id="fixture-run",
    )

    bundle.verify_checksum()
    assert len(bundle.evidence) == 1
    assert {signal.signal_type for signal in bundle.signals} == {
        "citation.visibility_change",
        "citation.competitor_visibility_change",
    }
    assert any(signal.subject_company == "Aprio" for signal in bundle.signals)
    assert any(signal.subject_company == "BDO" for signal in bundle.signals)
    assert all(signal.cluster_id == "govcon" for signal in bundle.signals)
    assert bundle.evidence[0].payload["citation_deltas"]


def test_citation_bundle_id_and_checksum_are_repeatable(
    raw_aprio_pair: tuple[RawMonitoringRun, RawMonitoringRun],
) -> None:
    runs = [normalize_run(raw, {"Aprio", "BDO"}) for raw in raw_aprio_pair]
    report = build_report(runs, "Aprio", client_id=str(CLIENT_ID)).model_dump(mode="json")

    first = build_citation_bundle(
        runs, report, producer_version="0.1.0", producer_run_id="same-run"
    )
    second = build_citation_bundle(
        runs, report, producer_version="0.1.0", producer_run_id="same-run"
    )

    assert first.bundle_id == second.bundle_id
    assert first.checksum == second.checksum
