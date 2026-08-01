from __future__ import annotations

from dataclasses import dataclass

from aivc.contracts.models import Signal


@dataclass(frozen=True)
class CorrelatedSignals:
    cluster_id: str
    citation: tuple[Signal, ...]
    recon: tuple[Signal, ...]


def correlate_signals(
    citation: tuple[Signal, ...], recon: tuple[Signal, ...]
) -> list[CorrelatedSignals]:
    """Correlate only exact client-prevalidated cluster IDs."""
    cluster_ids = sorted(
        {
            str(signal.cluster_id or "unclustered")
            for signal in (*citation, *recon)
        }
    )
    return [
        CorrelatedSignals(
            cluster_id=cluster_id,
            citation=tuple(
                signal
                for signal in citation
                if str(signal.cluster_id or "unclustered") == cluster_id
            ),
            recon=tuple(
                signal
                for signal in recon
                if str(signal.cluster_id or "unclustered") == cluster_id
            ),
        )
        for cluster_id in cluster_ids
    ]
