from uuid import UUID, uuid4

from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    ClientIdentity,
    ProducerIdentity,
    Signal,
    SignalBundle,
)
from scout.keys import make_trigger_key
from scout.models.sov import InvestigationTrigger
from scout.nodes.citation_adapter import canonical_citation_analysis


def _trigger() -> InvestigationTrigger:
    return InvestigationTrigger(
        client_id="b88e87f3-0aa5-4da9-be48-2807b12d5a91",
        client_name="Aprio",
        competitor_name="BDO",
        competitor_domain="bdo.com",
        cluster_id="govcon",
        cluster_label="Government Contracting",
        shift_type="gain",
        shift_magnitude=5,
        investigation_priority="standard",
        triage_reason="fixture trigger",
    )


def _bundle(client_id: UUID) -> SignalBundle:
    return SignalBundle(
        bundle_id="bundle-1",
        created_at="2026-08-01T00:00:00Z",
        producer=ProducerIdentity(name="ai_visibility", version="0.1.0", run_id="run-1"),
        client=ClientIdentity(client_id=client_id, canonical_name="Aprio"),
        analysis_period=AnalysisPeriod(),
        status=BundleStatus.complete,
        signals=[
            Signal(
                signal_id="signal-bdo",
                signal_type="citation.competitor_visibility_change",
                subject_company="BDO",
                cluster_id="govcon",
                provider="gemini",
                query="Who provides government contracting advice?",
                direction="increase",
                confidence="high",
            )
        ],
    ).sealed()


def test_adapter_matches_exact_client_competitor_and_cluster() -> None:
    trigger = _trigger()
    result = canonical_citation_analysis(
        {
            "investigation_triggers": [trigger],
            "clients": [],
            "citation_bundle": _bundle(UUID(trigger.client_id)).model_dump(mode="json"),
        }
    )
    key = make_trigger_key(trigger.client_id, trigger.competitor_name, trigger.cluster_id)

    evidence = result["ai_citation_changes"][key]
    assert evidence.abstained is False
    assert evidence.matched_signal_ids == ["signal-bdo"]


def test_adapter_abstains_on_cross_client_bundle() -> None:
    trigger = _trigger()
    result = canonical_citation_analysis(
        {
            "investigation_triggers": [trigger],
            "clients": [],
            "citation_bundle": _bundle(uuid4()).model_dump(mode="json"),
        }
    )
    key = make_trigger_key(trigger.client_id, trigger.competitor_name, trigger.cluster_id)

    evidence = result["ai_citation_changes"][key]
    assert evidence.abstained is True
    assert evidence.warnings == ["canonical_citation_bundle_unavailable"]
