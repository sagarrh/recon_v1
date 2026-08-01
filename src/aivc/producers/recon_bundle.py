from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from aivc.contracts.models import (
    AnalysisPeriod,
    BundleStatus,
    ClientIdentity,
    EvidenceArtifact,
    MeasuredMetric,
    ProducerIdentity,
    Signal,
    SignalBundle,
    stable_id,
)
from aivc.contracts.validation import validate_bundle_schema


def _dump(value: object) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise TypeError(f"Unsupported Recon artifact type: {type(value).__name__}")


def build_recon_bundle(
    state: dict[str, Any],
    client: ClientIdentity,
    *,
    producer_version: str,
    parent_run_id: UUID | None = None,
    persistence_failures: dict[str, str] | None = None,
) -> SignalBundle:
    """Export measured Recon artifacts without reinterpreting them."""
    run_id = str(state["run_id"])
    evidence: list[EvidenceArtifact] = []
    trigger_evidence_ids: dict[tuple[str, str], str] = {}
    trigger_labels: dict[tuple[str, str], str] = {}
    for trigger_value in state.get("investigation_triggers", []):
        trigger = _dump(trigger_value)
        if str(trigger.get("triage_severity") or "").upper() == "NOISE":
            continue
        trigger_hash = stable_id(
            run_id,
            trigger["client_id"],
            trigger["cluster_id"],
            trigger["competitor_name"],
        )
        evidence_id = f"recon-trigger:{trigger_hash}"
        trigger_evidence_ids[(str(trigger["cluster_id"]), str(trigger["competitor_name"]))] = (
            evidence_id
        )
        trigger_labels[(str(trigger["cluster_id"]), str(trigger["competitor_name"]))] = str(
            trigger.get("cluster_label") or trigger["cluster_id"]
        )
        evidence.append(
            EvidenceArtifact(
                evidence_id=evidence_id,
                kind="recon.investigation_trigger",
                source_ref=f"cycle_run:{run_id}",
                observed_at=f"{state['sync_date']}T00:00:00Z",
                payload=trigger,
            )
        )

    signals: list[Signal] = []
    for record_value in state.get("sov_tracking_records", []):
        record = _dump(record_value)
        if str(record.get("client_id")) != str(client.client_id):
            raise ValueError("Recon state contains SOV evidence for a different client")
        if not record.get("alert_triggered"):
            continue
        competitor = str(record["competitor_name"])
        cluster_id = str(record["cluster_id"])
        delta = record.get("change_vs_avg")
        delta_value = None if delta is None else float(delta)
        current_sov = float(record["sov_score"])
        trigger_key = (cluster_id, competitor)
        if trigger_key not in trigger_evidence_ids:
            continue
        if delta_value is None and current_sov <= 0:
            continue
        first_observation = delta_value is None
        signal_key = stable_id(run_id, client.client_id, cluster_id, competitor, "sov")
        signals.append(
            Signal(
                signal_id=signal_key,
                signal_type=(
                    "recon.sov_first_observation"
                    if first_observation
                    else "recon.sov_change"
                ),
                subject_company=competitor,
                cluster_id=cluster_id,
                cluster_label=trigger_labels[trigger_key],
                current_observed_at=f"{record['week_date']}T00:00:00Z",
                direction=(
                    "increase"
                    if delta_value is not None and delta_value > 0
                    else "decrease"
                    if delta_value is not None and delta_value < 0
                    else "new"
                    if first_observation
                    else "unknown"
                ),
                magnitude=MeasuredMetric(
                    name=("current_sov" if first_observation else "sov_change_vs_baseline"),
                    value=current_sov if first_observation else delta_value,
                    unit="percentage_points",
                ),
                metrics=[
                    MeasuredMetric(
                        name="current_sov",
                        value=current_sov,
                        unit="percentage_points",
                    ),
                    MeasuredMetric(
                        name="z_score",
                        value=(
                            float(record["z_score"])
                            if record.get("z_score") is not None
                            else None
                        ),
                        unit="score",
                    ),
                ],
                confidence="medium" if record.get("z_score") is None else "high",
                evidence_refs=[trigger_evidence_ids[trigger_key]],
                warnings=([] if delta_value is not None else ["sov_first_observation"]),
                source_payload={
                    "alert_type": record.get("alert_type"),
                    "alert_reason": record.get("alert_reason"),
                },
            )
        )

    recommendations = [_dump(item) for item in state.get("recommendations", [])]
    flags = sorted((persistence_failures or {}).keys())
    bundle = SignalBundle(
        bundle_id=stable_id("scout", client.client_id, run_id, producer_version),
        created_at=datetime.now(UTC),
        producer=ProducerIdentity(
            name="scout",
            version=producer_version,
            run_id=run_id,
            parent_run_id=parent_run_id,
        ),
        client=client,
        analysis_period=AnalysisPeriod(
            start=f"{state['sync_date']}T00:00:00Z",
            end=f"{state['sync_date']}T23:59:59Z",
        ),
        status=BundleStatus.partial if flags else BundleStatus.complete,
        signals=signals,
        recommendations=recommendations,
        evidence=evidence,
        data_quality_flags=[f"recon_persistence_failed:{name}" for name in flags],
    ).sealed()
    bundle.verify_checksum()
    validate_bundle_schema(bundle.model_dump(mode="json"))
    return bundle
