from datetime import date
from uuid import uuid4

from aivc.contracts.models import ClientIdentity
from aivc.producers.recon_bundle import build_recon_bundle
from scout.models.sov import InvestigationTrigger, SOVTrackingRecord


def test_recon_bundle_exports_triggered_measured_sov() -> None:
    client_id = uuid4()
    trigger = InvestigationTrigger(
        client_id=str(client_id),
        client_name="Aprio",
        competitor_name="BDO",
        competitor_domain="bdo.com",
        cluster_id="govcon",
        cluster_label="Government Contracting",
        shift_type="gain",
        shift_magnitude=5,
        investigation_priority="standard",
        triage_reason="fixture",
    )
    record = SOVTrackingRecord(
        client_id=str(client_id),
        cluster_id="govcon",
        competitor_name="BDO",
        week_date=date(2026, 8, 1),
        sov_score=22,
        change_vs_avg=5,
        z_score=2.2,
        alert_triggered=True,
        alert_type="gain",
    )

    bundle = build_recon_bundle(
        {
            "run_id": "recon-run",
            "sync_date": "2026-08-01",
            "investigation_triggers": [trigger],
            "sov_tracking_records": [record],
            "recommendations": [],
        },
        ClientIdentity(client_id=client_id, canonical_name="Aprio"),
        producer_version="0.1.0",
    )

    bundle.verify_checksum()
    assert len(bundle.signals) == 1
    assert bundle.signals[0].magnitude.value == 5
    assert bundle.signals[0].cluster_label == "Government Contracting"
    assert bundle.signals[0].evidence_refs == [bundle.evidence[0].evidence_id]


def test_recon_bundle_excludes_noise_and_zero_baseline_records() -> None:
    client_id = uuid4()
    trigger = InvestigationTrigger(
        client_id=str(client_id),
        client_name="Aprio",
        competitor_name="BDO",
        competitor_domain="bdo.com",
        cluster_id="bad-cluster",
        cluster_label="Unrelated topic",
        shift_type="gain",
        shift_magnitude=0,
        investigation_priority="standard",
        triage_reason="routine news mode",
        triage_severity="NOISE",
    )
    record = SOVTrackingRecord(
        client_id=str(client_id),
        cluster_id="bad-cluster",
        competitor_name="BDO",
        week_date=date(2026, 8, 1),
        sov_score=0,
        alert_triggered=True,
        alert_type="gain",
    )

    bundle = build_recon_bundle(
        {
            "run_id": "recon-run",
            "sync_date": "2026-08-01",
            "investigation_triggers": [trigger],
            "sov_tracking_records": [record],
            "recommendations": [],
        },
        ClientIdentity(client_id=client_id, canonical_name="Aprio"),
        producer_version="0.1.0",
    )

    assert bundle.signals == []
    assert bundle.evidence == []


def test_recon_bundle_labels_positive_first_observation() -> None:
    client_id = uuid4()
    trigger = InvestigationTrigger(
        client_id=str(client_id),
        client_name="Aprio",
        competitor_name="BDO",
        competitor_domain="bdo.com",
        cluster_id="govcon",
        cluster_label="Government Contracting",
        shift_type="new_entrant",
        shift_magnitude=8,
        investigation_priority="standard",
        triage_reason="first observation",
        triage_severity="WATCH",
    )
    record = SOVTrackingRecord(
        client_id=str(client_id),
        cluster_id="govcon",
        competitor_name="BDO",
        week_date=date(2026, 8, 1),
        sov_score=8,
        alert_triggered=True,
        alert_type="new_entrant",
    )

    bundle = build_recon_bundle(
        {
            "run_id": "recon-run",
            "sync_date": "2026-08-01",
            "investigation_triggers": [trigger],
            "sov_tracking_records": [record],
            "recommendations": [],
        },
        ClientIdentity(client_id=client_id, canonical_name="Aprio"),
        producer_version="0.1.0",
    )

    assert bundle.signals[0].signal_type == "recon.sov_first_observation"
    assert bundle.signals[0].direction == "new"
    assert bundle.signals[0].magnitude.value == 8
