from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    EvidenceArtifact,
    ProducerIdentity,
    SignalBundle,
    stable_id,
)
from aivc.contracts.validation import validate_bundle_schema


def compose_bundles(
    citation: SignalBundle,
    recon: SignalBundle,
    *,
    producer_version: str,
    parent_run_id: UUID,
) -> SignalBundle:
    """Compose two verified producer bundles with strict client/evidence checks."""
    citation.verify_checksum()
    recon.verify_checksum()
    if citation.client.client_id != recon.client.client_id:
        raise ValueError("cannot compose bundles for different clients")

    evidence: dict[str, EvidenceArtifact] = {}
    for artifact in [*citation.evidence, *recon.evidence]:
        existing = evidence.get(artifact.evidence_id)
        if existing is not None and existing != artifact:
            raise ValueError(f"conflicting evidence ID: {artifact.evidence_id}")
        evidence[artifact.evidence_id] = artifact

    starts = [
        period
        for period in (citation.analysis_period.start, recon.analysis_period.start)
        if period
    ]
    ends = [
        period
        for period in (citation.analysis_period.end, recon.analysis_period.end)
        if period
    ]
    source_checksums = sorted([str(citation.checksum), str(recon.checksum)])
    status = (
        BundleStatus.complete
        if citation.status == BundleStatus.complete and recon.status == BundleStatus.complete
        else BundleStatus.partial
    )
    combined = SignalBundle(
        bundle_id=stable_id(
            "aivc_combined", citation.client.client_id, *source_checksums, producer_version
        ),
        created_at=datetime.now(UTC),
        producer=ProducerIdentity(
            name="aivc_combined",
            version=producer_version,
            run_id=str(parent_run_id),
            parent_run_id=parent_run_id,
        ),
        client=citation.client,
        analysis_period=AnalysisPeriod(
            start=min(starts) if starts else None,
            end=max(ends) if ends else None,
        ),
        status=status,
        signals=[*citation.signals, *recon.signals],
        recommendations=[*citation.recommendations, *recon.recommendations],
        evidence=list(evidence.values()),
        reports=[*citation.reports, *recon.reports],
        data_quality_flags=sorted(
            set(citation.data_quality_flags + recon.data_quality_flags)
        ),
        source_bundle_ids=[citation.bundle_id, recon.bundle_id],
    ).sealed()
    combined.verify_checksum()
    validate_bundle_schema(combined.model_dump(mode="json"), combined=True)
    return combined
