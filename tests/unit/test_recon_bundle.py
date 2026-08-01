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
    assert bundle.signals[0].evidence_refs == [bundle.evidence[0].evidence_id]
