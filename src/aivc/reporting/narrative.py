from __future__ import annotations

from typing import Protocol

from aivc.reporting.models import FinalReportSnapshot


class NarrativeProvider(Protocol):
    def enrich(self, snapshot: FinalReportSnapshot) -> FinalReportSnapshot: ...


class ReuseValidatedNarrative:
    """Initial no-extra-LLM policy: retain validated deterministic/Recon prose."""

    def enrich(self, snapshot: FinalReportSnapshot) -> FinalReportSnapshot:
        snapshot.verify_checksum()
        return snapshot
