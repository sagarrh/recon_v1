from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    ClientIdentity,
    MeasuredMetric,
    ProducerIdentity,
    Signal,
    SignalBundle,
    stable_id,
)


def _bundle() -> SignalBundle:
    client_id = uuid4()
    return SignalBundle(
        bundle_id=stable_id("ai_visibility", client_id, "run-1"),
        created_at=datetime.now(UTC),
        producer=ProducerIdentity(name="ai_visibility", version="0.1.0", run_id="run-1"),
        client=ClientIdentity(client_id=client_id, canonical_name="Aprio"),
        analysis_period=AnalysisPeriod(
            start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC)
        ),
        status=BundleStatus.complete,
        signals=[
            Signal(
                signal_id=stable_id("signal", 1),
                signal_type="citation.visibility_increase",
                subject_company="Aprio",
                direction="increase",
                magnitude=MeasuredMetric(name="visibility_delta", value=0.2, unit="ratio"),
                confidence="medium",
            )
        ],
    )


def test_signal_bundle_checksum_is_stable_and_verifiable() -> None:
    sealed = _bundle().sealed()
    sealed.verify_checksum()
    changed_timestamp = sealed.model_copy(update={"created_at": datetime.now(UTC)})
    assert changed_timestamp.computed_checksum() == sealed.checksum


def test_signal_bundle_rejects_unknown_contract_fields() -> None:
    payload = _bundle().model_dump()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        SignalBundle.model_validate(payload)


def test_analysis_period_rejects_reverse_order() -> None:
    with pytest.raises(ValidationError):
        AnalysisPeriod(start=datetime(2026, 2, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC))
