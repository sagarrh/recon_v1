from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aivc.bundles import compose_bundles
from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    ClientIdentity,
    ProducerIdentity,
    SignalBundle,
)


def _bundle(name: str, client: ClientIdentity) -> SignalBundle:
    return SignalBundle(
        bundle_id=f"{name}-bundle",
        created_at=datetime.now(UTC),
        producer=ProducerIdentity(name=name, version="0.1.0", run_id=f"{name}-run"),
        client=client,
        analysis_period=AnalysisPeriod(
            start="2026-01-01T00:00:00Z", end="2026-02-01T00:00:00Z"
        ),
        status=BundleStatus.complete,
    ).sealed()


def test_compose_bundles_verifies_and_records_sources() -> None:
    client = ClientIdentity(client_id=uuid4(), canonical_name="Aprio")
    citation = _bundle("ai_visibility", client)
    recon = _bundle("scout", client)

    combined = compose_bundles(
        citation, recon, producer_version="0.1.0", parent_run_id=uuid4()
    )

    combined.verify_checksum()
    assert combined.status == BundleStatus.complete
    assert combined.source_bundle_ids == [citation.bundle_id, recon.bundle_id]


def test_compose_bundles_rejects_cross_client_inputs() -> None:
    citation = _bundle(
        "ai_visibility", ClientIdentity(client_id=uuid4(), canonical_name="Aprio")
    )
    recon = _bundle("scout", ClientIdentity(client_id=uuid4(), canonical_name="Other"))

    with pytest.raises(ValueError, match="different clients"):
        compose_bundles(citation, recon, producer_version="0.1.0", parent_run_id=uuid4())
