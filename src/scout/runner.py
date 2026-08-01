from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from aivc.contracts.models import SignalBundle
from scout.db.reader import load_data
from scout.db.sed_writer import finish_run, start_run
from scout.db.supabase_client import get_sed_client
from scout.db.writer import write_outputs
from scout.graph import build_graph
from scout.llm import flush_prompt_log, get_token_total, set_run_id, validate_prompts
from scout.models.sov import CycleSummary
from scout.nodes.slack_delivery import slack_delivery

REQUIRED_PERSISTENCE_GROUPS = {
    "sov_tracking",
    "investigation_triggers",
    "investigations",
    "recommendations",
    "reports",
}


def prepare_recon_data(*, client_id: str) -> dict[str, Any]:
    return load_data(client_id=client_id)


def run_recon(
    prepared: dict[str, Any],
    citation_bundle: SignalBundle,
    *,
    deliver: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Recon for one pre-resolved client; persist before optional delivery."""
    if len(prepared.get("clients", [])) != 1:
        raise ValueError("Recon requires exactly one pre-resolved client")
    client = prepared["clients"][0]
    if str(client.get("client_id")) != str(citation_bundle.client.client_id):
        raise ValueError("Recon input and citation bundle client IDs differ")

    validate_prompts()
    run_id = str(uuid4())
    set_run_id(run_id)
    state: dict[str, Any] = {
        "run_id": run_id,
        "sync_date": prepared["sync_date"],
        "clients": prepared["clients"],
        "sov_tracking_records": [],
        "investigation_triggers": [],
        "cluster_verdicts": [],
        "triage_digest": [],
        "website_changes": {},
        "third_party_signals": {},
        "ai_citation_changes": {},
        "citation_bundle": citation_bundle.model_dump(mode="json"),
        "client_readiness": {},
        "historical_context": {},
        "recommendations": [],
        "internal_reports": [],
        "client_summaries": [],
        "cycle_summary": None,
        "blog_detections": {},
        "blog_investigation_triggers": [],
        "numeric_provenance_metric": {},
    }
    sb = get_sed_client()
    start_run(sb, run_id, prepared["sync_date"], "live", client.get("client_name"))
    try:
        final_state = dict(build_graph(include_delivery=False).invoke(state))
        prompt_tokens, completion_tokens = get_token_total()
        cycle = final_state.get("cycle_summary")
        if cycle and hasattr(cycle, "model_dump"):
            cycle_data = cycle.model_dump()
            cycle_data["total_tokens_used"] = prompt_tokens + completion_tokens
            final_state["cycle_summary"] = CycleSummary(**cycle_data)

        persistence = write_outputs(final_state)
        failures = persistence.get("failures", {})
        required_failures = sorted(REQUIRED_PERSISTENCE_GROUPS & failures.keys())
        persistence["required_failures"] = required_failures
        persistence["optional_failures"] = sorted(failures.keys() - REQUIRED_PERSISTENCE_GROUPS)
        if required_failures:
            raise RuntimeError(
                "Required Recon persistence failed: " + ", ".join(required_failures)
            )

        if deliver:
            slack_delivery(final_state)
        flush_prompt_log()
    except Exception as exc:
        try:
            flush_prompt_log()
            finish_run(sb, run_id, "failed", error_message=str(exc))
        except Exception:
            logging.getLogger(__name__).exception(
                "failed to mark Recon cycle run %s as failed", run_id
            )
        raise

    finish_run(
        sb,
        run_id,
        "completed",
        {
            "total_tokens": prompt_tokens + completion_tokens,
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
            "clients_processed": 1,
            "triggers_fired": len(final_state.get("investigation_triggers", [])),
        },
    )
    return final_state, persistence
