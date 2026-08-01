from __future__ import annotations

from typing import Any

from aivc.contracts.models import SignalBundle
from scout.keys import make_trigger_key
from scout.models.investigation import CitationInvestigationEvidence
from scout.state import ScoutState


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _payload(bundle: object) -> SignalBundle | None:
    if isinstance(bundle, SignalBundle):
        return bundle
    if isinstance(bundle, dict) and bundle:
        return SignalBundle.model_validate(bundle)
    return None


def canonical_citation_analysis(state: ScoutState) -> dict[str, Any]:
    """Match measured citation signals to Recon triggers without an LLM."""
    bundle = _payload(state.get("citation_bundle", {}))
    output: dict[str, CitationInvestigationEvidence] = {}
    for trigger in state.get("investigation_triggers", []):
        key = make_trigger_key(trigger.client_id, trigger.competitor_name, trigger.cluster_id)
        if bundle is None or str(bundle.client.client_id) != str(trigger.client_id):
            output[key] = CitationInvestigationEvidence(
                client_id=trigger.client_id,
                competitor_name=trigger.competitor_name,
                cluster_id=trigger.cluster_id,
                abstained=True,
                abstention_counts={"matched_signals": 0},
                warnings=["canonical_citation_bundle_unavailable"],
            )
            continue

        aliases = {_normalized(trigger.competitor_name)}
        for client in state.get("clients", []):
            if str(client.get("client_id")) != str(trigger.client_id):
                continue
            for cluster in client.get("clusters", []):
                if str(cluster.get("cluster_id")) != str(trigger.cluster_id):
                    continue
                for competitor in cluster.get("competitors", []):
                    if _normalized(str(competitor.get("competitor_name", ""))) in aliases:
                        aliases.update(
                            _normalized(str(alias))
                            for alias in competitor.get("aliases", [])
                            if str(alias).strip()
                        )

        matched = [
            signal
            for signal in bundle.signals
            if signal.cluster_id == trigger.cluster_id
            and _normalized(signal.subject_company) in aliases
        ]
        if not matched:
            output[key] = CitationInvestigationEvidence(
                client_id=trigger.client_id,
                competitor_name=trigger.competitor_name,
                cluster_id=trigger.cluster_id,
                abstained=True,
                abstention_counts={"matched_signals": 0},
                warnings=["no_exact_citation_signal_match"],
            )
            continue

        evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
        evidence = [
            evidence_by_id[evidence_ref]
            for signal in matched
            for evidence_ref in signal.evidence_refs
            if evidence_ref in evidence_by_id
        ]
        output[key] = CitationInvestigationEvidence(
            client_id=trigger.client_id,
            competitor_name=trigger.competitor_name,
            cluster_id=trigger.cluster_id,
            matched_signal_ids=[signal.signal_id for signal in matched],
            provider_query_changes=[
                {
                    "provider": signal.provider,
                    "query": signal.query,
                    "monitor_query_key": signal.monitor_query_key,
                    "previous_observed_at": signal.previous_observed_at,
                    "current_observed_at": signal.current_observed_at,
                }
                for signal in matched
            ],
            visibility_changes=[
                {
                    "direction": signal.direction,
                    "magnitude": (
                        signal.magnitude.model_dump(mode="json") if signal.magnitude else None
                    ),
                    "metrics": [metric.model_dump(mode="json") for metric in signal.metrics],
                }
                for signal in matched
            ],
            citation_source_changes=[
                delta
                for artifact in evidence
                for delta in artifact.payload.get("citation_deltas", [])
            ],
            page_evidence_refs=sorted(
                {
                    evidence_ref
                    for signal in matched
                    for evidence_ref in signal.evidence_refs
                }
            ),
            confidence=(
                "high"
                if all(signal.confidence == "high" for signal in matched)
                else "medium"
            ),
            warnings=sorted({warning for signal in matched for warning in signal.warnings}),
            abstention_counts={"matched_signals": len(matched)},
        )
    return {"ai_citation_changes": output}
