from __future__ import annotations

from dataclasses import dataclass

from aivc.contracts.models import EvidenceArtifact, Signal, SignalBundle


@dataclass(frozen=True)
class PublicationResult:
    citation_signals: tuple[Signal, ...]
    recon_signals: tuple[Signal, ...]
    recommendations: tuple[dict[str, object], ...]
    evidence: dict[str, EvidenceArtifact]
    quality_flags: tuple[str, ...]
    blockers: tuple[str, ...]


def _is_material_citation(signal: Signal) -> bool:
    if signal.signal_type != "citation.visibility_change" or signal.magnitude is None:
        return False
    value = abs(float(signal.magnitude.value or 0.0))
    numerator = abs(float(signal.magnitude.numerator or 0.0))
    return value >= 0.1 or numerator >= 2


def _is_publishable_recon(signal: Signal) -> bool:
    if not signal.signal_type.startswith("recon.sov_") or signal.magnitude is None:
        return False
    current = next(
        (metric.value for metric in signal.metrics if metric.name == "current_sov"), None
    )
    magnitude = signal.magnitude.value
    if signal.direction == "unknown":
        return False
    if float(current or 0.0) == 0.0 and float(magnitude or 0.0) == 0.0:
        return False
    return "noise" not in {warning.casefold() for warning in signal.warnings}


def assess_publication(
    citation: SignalBundle,
    recon: SignalBundle,
    combined: SignalBundle,
) -> PublicationResult:
    """Verify bundle identity/checksums and retain only publishable evidence."""
    blockers: list[str] = []
    for bundle in (citation, recon, combined):
        try:
            bundle.verify_checksum()
        except ValueError:
            blockers.append(f"invalid_bundle_checksum:{bundle.producer.name}")
    client_ids = {citation.client.client_id, recon.client.client_id, combined.client.client_id}
    if len(client_ids) != 1:
        blockers.append("client_identity_mismatch")
    if set(combined.source_bundle_ids) != {citation.bundle_id, recon.bundle_id}:
        blockers.append("combined_source_bundle_mismatch")

    evidence = {item.evidence_id: item for item in combined.evidence}
    citation_signals = tuple(
        signal
        for signal in citation.signals
        if _is_material_citation(signal)
        and signal.evidence_refs
        and all(ref in evidence for ref in signal.evidence_refs)
    )
    recon_signals = tuple(
        signal
        for signal in recon.signals
        if _is_publishable_recon(signal)
        and signal.evidence_refs
        and all(ref in evidence for ref in signal.evidence_refs)
    )
    publishable_recon_keys = {
        (str(signal.cluster_id or ""), signal.subject_company.casefold())
        for signal in recon_signals
    }
    recommendations = tuple(
        recommendation
        for recommendation in recon.recommendations
        if str(recommendation.get("validation_status", "ok")) != "quarantined"
        and (
            str(recommendation.get("cluster_id") or ""),
            str(recommendation.get("competitor_name") or "").casefold(),
        )
        in publishable_recon_keys
    )
    flags = sorted(set(combined.data_quality_flags))
    blockers.extend(
        flag for flag in flags if flag.startswith("recon_persistence_failed:")
    )
    return PublicationResult(
        citation_signals=citation_signals,
        recon_signals=recon_signals,
        recommendations=recommendations,
        evidence=evidence,
        quality_flags=tuple(flags),
        blockers=tuple(sorted(set(blockers))),
    )
