# ai_response_analysis.py — LangGraph node: AI citation diff analysis for investigation triggers.
# Purpose: Calls Gemini with baseline vs current AI response bundles to produce structured AICitationChange per trigger.
# Scope: Per-trigger LLM call, validation + repair fallback, new_entrant-aware baseline skip, typed result dict.
# Consumers: scout/graph.py registers this as ai_response_analysis; recommendation_gen reads the resulting dict.
import json

from scout.config import get_config
from scout.db.cache import get_ai_response_bundle, get_geo_ai_baseline_bundle
from scout.evidence_floor import ai_paired_weeks, below_floor
from scout.keys import make_trigger_key
from scout.llm import call_synthesis, load_prompt
from scout.models.investigation import AICitationChange
from scout.nodes.enrichment import enrich_ai_responses
from scout.state import ScoutState
from scout.utils import is_transient

_FALLBACK_AI_CITATION = {
    "previous_positioning": "not previously cited",
    "current_positioning": "unknown — data unavailable",
    "citation_frequency_change": {},
    "positioning_shifts": [],
    "new_claims": [],
    "dropped_claims": [],
    "inferred_trigger": None,
    "delta_summary": "No AI response data available — evidence source unavailable for this trigger.",
}


def ai_response_analysis(state: ScoutState) -> dict:
    """LangGraph node: for each investigation trigger, produce an AICitationChange describing positioning deltas.
    Returns {'ai_citation_changes': {trigger_key: AICitationChange}}; reads response bundles from the Supabase AI-response cache."""
    system_prompt = load_prompt("scout-ai-response-analysis")
    triggers = state["investigation_triggers"]
    sync_date = state.get("sync_date")
    ai_citation_changes: dict[str, AICitationChange] = {}

    for trigger in triggers:
        key = make_trigger_key(trigger.client_id, trigger.competitor_name, trigger.cluster_id)
        print(f"[ai_response_analysis] Processing {key}")

        response_bundle = get_ai_response_bundle(trigger.cluster_id, sync_date) or None
        if response_bundle is None:
            response_bundle = get_geo_ai_baseline_bundle(trigger.client_id, trigger.cluster_id) or None

        if response_bundle is None:
            ai_citation_changes[key] = _fallback(trigger.competitor_name, trigger.cluster_id)
            continue

        is_new_entrant = trigger.shift_type == "new_entrant"

        # R3-1/R3-2: deterministic minimum-evidence floor — below it (and not a baseline-exempt new entrant),
        # enrich first (wider response window), then skip the LLM (no prompt_log row) only when enrichment
        # still can't clear the floor. The bundle caps each side to one row, so a one-sided bundle = 0 paired.
        paired = ai_paired_weeks(response_bundle)
        if not is_new_entrant and below_floor(paired, get_config().ai_resp_min_paired_weeks):
            if get_config().enrichment_enabled:
                enriched = enrich_ai_responses(trigger, sync_date)
                if enriched:
                    response_bundle = enriched
                paired = ai_paired_weeks(response_bundle)
            if below_floor(paired, get_config().ai_resp_min_paired_weeks):
                print(f"[ai_response_analysis] {key} — below evidence floor ({paired} paired weeks) after enrichment, abstaining")
                ab = _fallback(trigger.competitor_name, trigger.cluster_id)
                ab.abstained = True
                ab.abstention_counts = {"paired_weeks": paired}
                ai_citation_changes[key] = ab
                continue

        baseline_note = (
            "NOTE: This competitor is a new entrant. There are no baseline responses. "
            "Skip baseline comparison and extract only current claims and positioning."
            if is_new_entrant
            else ""
        )

        user_message = (
            f"Perform AI citation diff analysis for competitor {trigger.competitor_name} "
            f"on cluster '{trigger.cluster_label}' (shift_type: {trigger.shift_type}).\n\n"
            f"{baseline_note}\n\n"
            f"Response data:\n{json.dumps(response_bundle)}\n\n"
            f"Return a JSON object matching this schema exactly:\n"
            f"{{\n"
            f'  "competitor_name": "{trigger.competitor_name}",\n'
            f'  "cluster_id": "{trigger.cluster_id}",\n'
            f'  "previous_positioning": "string (use \'not previously cited\' for new entrants)",\n'
            f'  "current_positioning": "string",\n'
            f'  "citation_frequency_change": {{"total_queries": int, "cited_baseline": int, "cited_current": int}},\n'
            f'  "positioning_shifts": [list of {{"dimension": str, "before": str, "after": str}}],\n'
            f'  "new_claims": [list of new claim strings],\n'
            f'  "dropped_claims": [list of dropped claim strings],\n'
            f'  "inferred_trigger": "string or null",\n'
            f'  "delta_summary": "2-3 sentence forensic diff summary"\n'
            f"}}"
        )

        result = _call_with_fallback(
            system_prompt, user_message, trigger.competitor_name, trigger.cluster_id, key
        )
        ai_citation_changes[key] = result

    return {"ai_citation_changes": ai_citation_changes}



def _call_with_fallback(
    system_prompt: str,
    user_message: str,
    competitor_name: str,
    cluster_id: str,
    trigger_key: str,
) -> AICitationChange:
    """Invoke Gemini with one transient-retry and a two-step validate-or-repair pipeline before falling back.
    Returns a best-effort AICitationChange; any non-transient error collapses to the fallback payload."""
    for attempt in range(2):
        try:
            raw = call_synthesis(
                system_prompt=system_prompt,
                user_message=user_message,
                expect_json=True,
                node_name="ai_response_analysis",
                trigger_key=trigger_key,
            )
            raw["competitor_name"] = competitor_name
            raw["cluster_id"] = cluster_id
            validated = _validate_and_repair(raw, competitor_name, cluster_id)
            if validated:
                return validated
            print(f"[ai_response_analysis] output validation failed for {trigger_key}, using fallback")
            return _fallback(competitor_name, cluster_id)
        except Exception as e:
            if is_transient(e) and attempt == 0:
                continue
            print(f"[ai_response_analysis] API error for {trigger_key}: {e}")
            return _fallback(competitor_name, cluster_id)
    return _fallback(competitor_name, cluster_id)


def _validate_and_repair(raw: dict, competitor_name: str, cluster_id: str) -> AICitationChange | None:
    """Try strict AICitationChange validation; on failure patch common missing fields and retry once.
    Returns None if even the repaired dict still fails schema validation, signaling the caller to fall back."""
    try:
        return AICitationChange(**raw)
    except Exception:
        pass
    repaired = dict(raw)
    repaired.setdefault("previous_positioning", "not previously cited")
    repaired.setdefault("current_positioning", "unknown")
    repaired.setdefault("citation_frequency_change", {})
    repaired.setdefault("positioning_shifts", [])
    repaired.setdefault("new_claims", [])
    repaired.setdefault("dropped_claims", [])
    repaired.setdefault("inferred_trigger", None)
    if len(repaired.get("delta_summary", "")) < 20:
        repaired["delta_summary"] = "Partial AI citation data available — delta summary incomplete."
    repaired["competitor_name"] = competitor_name
    repaired["cluster_id"] = cluster_id
    try:
        return AICitationChange(**repaired)
    except Exception as e:
        print(f"[ai_response_analysis] repair failed: {e}")
        return None


def _fallback(competitor_name: str, cluster_id: str) -> AICitationChange:
    """Return the canonical 'evidence unavailable' AICitationChange stamped with competitor + cluster ids.
    Used whenever the LLM call fails, validation fails, or no response bundle exists for the trigger."""
    return AICitationChange(
        competitor_name=competitor_name,
        cluster_id=cluster_id,
        **_FALLBACK_AI_CITATION,
    )
