# web_intelligence.py — LangGraph node: third-party signal extraction from Bright Data SERP results.
# Purpose: Runs SERP queries per trigger, does a two-stage Grok extract+synthesis, returns typed ThirdPartySignals.
# Scope: SERP snippet formatting, two-call LLM pattern, validate+repair, fallback.
# Consumers: scout/graph.py registers this as web_intelligence; recommendation_gen consumes the resulting signals dict.
import json
import time

from scout.config import get_config
from scout.evidence_floor import below_floor, serp_usable_rows
from scout.integrations.bright_data import serp_search
from scout.keys import make_trigger_key
from scout.llm import call_extraction_consistent
from scout.models.investigation import ThirdPartySignals
from scout.nodes.enrichment import enrich_web_intel
from scout.state import ScoutState

_EXTRACT_SYSTEM = "You are a data extraction assistant. Extract ONLY what the search results explicitly state into the exact JSON schema. Do not infer. If a field has no findings set found=false and leave items empty."

_SYNTHESIS_SYSTEM = "You are a competitive intelligence analyst. Given pre-extracted signals from Google search results, produce the final analysis fields. Base inferred_trigger and confidence strictly on the evidence provided."

_FALLBACK_SIGNALS = {
    "queries_run": [],
    "review_activity": {"found": False, "platforms": [], "notable_activity": ""},
    "press_and_publications": {"found": False, "items": []},
    "directory_and_listings": {"found": False, "items": []},
    "linkedin_and_social": {"found": False, "notable_activity": ""},
    "awards_and_certifications": {"found": False, "items": []},
    "inferred_trigger": None,
    "confidence": "none",
    "summary": "No third-party signal data available — evidence source unavailable for this trigger.",
}


def web_intelligence(state: ScoutState) -> dict:
    """LangGraph node: for each trigger, harvest + distill third-party signals into a ThirdPartySignals model.
    Runs SERP queries then calls the two-stage Grok extract+synthesis pipeline."""
    triggers = state["investigation_triggers"]
    third_party_signals: dict[str, ThirdPartySignals] = {}

    co_mention_lookup: dict[str, dict] = {}
    if get_config().geo_comentions_enabled:
        try:
            from scout.db.client_context import get_co_mentions
            from scout.db.supabase_client import get_sed_client
            sb = get_sed_client()
            for t in triggers:
                if t.client_id not in co_mention_lookup:
                    co_mention_lookup[t.client_id] = get_co_mentions(sb, t.client_id)
        except Exception as e:
            print(f"[web_intelligence] co-mention lookup unavailable: {e}")

    for trigger in triggers:
        key = make_trigger_key(trigger.client_id, trigger.competitor_name, trigger.cluster_id)
        print(f"[web_intelligence] Processing {key}")

        signals_bundle = serp_search(
            trigger.competitor_name, trigger.cluster_label, trigger.shift_type
        )

        # R3-1/R3-2: deterministic minimum-evidence floor — below it, enrich first, then skip both Grok
        # calls (no prompt_log row) only when enrichment still can't clear the floor.
        rows = serp_usable_rows(signals_bundle.get("serp_results", []))
        if below_floor(rows, get_config().web_intel_min_serp_rows):
            if get_config().enrichment_enabled:
                signals_bundle = enrich_web_intel(trigger)
                rows = serp_usable_rows(signals_bundle.get("serp_results", []))
            if below_floor(rows, get_config().web_intel_min_serp_rows):
                print(f"[web_intelligence] {key} — below evidence floor ({rows} usable SERP rows) after enrichment, abstaining")
                ab = _fallback(trigger.competitor_name, trigger.cluster_id)
                ab.abstained = True
                ab.abstention_counts = {"usable_serp_rows": rows}
                third_party_signals[key] = ab
                continue

        n = co_mention_lookup.get(trigger.client_id, {}).get(trigger.competitor_name, 0)
        co_note = (
            f"\nCo-mention signal: {trigger.competitor_name} appears in {n} of this client's "
            f"selection_events (competitor co-occurrence in AI answers)." if n else ""
        )
        result = _extract_from_serp(trigger, signals_bundle, key, co_note)
        third_party_signals[key] = result

    return {"third_party_signals": third_party_signals}


def _format_snippets(serp_results: list[dict], categories: set[str]) -> str:
    """Render SERP results whose category is in the filter-set into a 'Query/Title/Snippet' plaintext block.
    Returns 'No results.' when nothing matches, used as the extraction prompt's evidence section."""
    lines = []
    for r in serp_results:
        if r.get("category") in categories:
            lines.append(f"Query: {r['query']}")
            for s in r.get("snippets", []):
                if s.get("title") or s.get("snippet"):
                    lines.append(f"  • {s['title']}: {s['snippet']}")
    return "\n".join(lines) or "No results."


def _extract_from_serp(trigger, signals_bundle: dict, trigger_key: str, co_note: str = "") -> ThirdPartySignals:
    """Run the two-stage Grok extract→synthesize flow on a SERP bundle and return a validated ThirdPartySignals.
    Falls back to the canonical 'evidence unavailable' payload when repair fails after both stages merge."""
    serp_results = signals_bundle.get("serp_results", [])
    queries_run = signals_bundle.get("queries_run", [])

    context = (
        f"Competitor: {trigger.competitor_name}\n"
        f"Cluster: {trigger.cluster_label}\n"
        f"Shift type: {trigger.shift_type}\n"
        f"Shift magnitude: {trigger.shift_magnitude}pp"
        f"{co_note}"
    )

    all_snippets = _format_snippets(serp_results, {"reviews", "press", "social", "awards"})
    extracted_signals = _grok_call(
        trigger_key, "call1_extraction",
        f"{context}\n\n--- Search Results ---\n{all_snippets}",
        '{"review_activity": {"found": bool, "platforms": [list of strings], "notable_activity": "string"},'
        '"press_and_publications": {"found": bool, "items": [{"source":"","headline":"","date":""}]},'
        '"directory_and_listings": {"found": bool, "items": [{"directory":"","detail":""}]},'
        '"linkedin_and_social": {"found": bool, "notable_activity": "string"},'
        '"awards_and_certifications": {"found": bool, "items": [list of strings]}}',
    )
    time.sleep(get_config().gemini_retry_delay_seconds)

    synthesis_input = (
        f"{context}\n\n"
        f"--- Pre-Extracted Signals ---\n{json.dumps(extracted_signals, indent=2)}\n\n"
        f"Based on the above signals, produce the final analysis:"
    )
    synthesis = _grok_call(
        trigger_key, "call2_synthesis",
        synthesis_input,
        '{"inferred_trigger": "most likely cause of the SOV shift or null",'
        '"confidence": "high|medium|low|none",'
        '"summary": "2-3 sentences on what was found and what it implies"}',
        system=_SYNTHESIS_SYSTEM,
    )

    merged = {
        "competitor_name": trigger.competitor_name,
        "cluster_id": trigger.cluster_id,
        "queries_run": [{"query": q["query"], "category": q.get("category", ""), "rationale": ""} for q in queries_run],
        **extracted_signals,
        **synthesis,
    }
    return _validate_and_repair(merged, trigger.competitor_name, trigger.cluster_id) \
        or _fallback(trigger.competitor_name, trigger.cluster_id)


def _grok_call(trigger_key: str, label: str, user_message: str, schema: str, system: str = _EXTRACT_SYSTEM) -> dict:
    """Wrap call_extraction_consistent with one retry (5s backoff) and return the raw dict, or {} on sustained failure.
    Uses EXTRACT system prompt by default; synthesis call overrides via the `system` parameter."""
    full_message = f"{user_message}\n\nExtract into this exact JSON schema:\n{schema}"
    for attempt in range(2):
        try:
            raw = call_extraction_consistent(
                system_prompt=system,
                user_message=full_message,
                expect_json=True,
                node_name="web_intelligence",
                trigger_key=f"{trigger_key}:{label}",
            )
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            if attempt == 0:
                time.sleep(5)
                continue
            print(f"[web_intelligence] {label} error for {trigger_key}: {e}")
            return {}
    return {}


def _validate_and_repair(raw: dict, competitor_name: str, cluster_id: str) -> ThirdPartySignals | None:
    """Try ThirdPartySignals strict validation; on failure patch missing nested dicts + confidence + summary, retry once.
    Returns None when repair still fails validation so the caller can fall through to _fallback()."""
    try:
        return ThirdPartySignals(**raw)
    except Exception:
        pass
    repaired = dict(raw)
    repaired.setdefault("queries_run", [])
    repaired.setdefault("review_activity", {"found": False, "platforms": [], "notable_activity": ""})
    repaired.setdefault("press_and_publications", {"found": False, "items": []})
    repaired.setdefault("directory_and_listings", {"found": False, "items": []})
    repaired.setdefault("linkedin_and_social", {"found": False, "notable_activity": ""})
    repaired.setdefault("awards_and_certifications", {"found": False, "items": []})
    repaired.setdefault("inferred_trigger", None)
    if repaired.get("confidence") not in {"high", "medium", "low", "none"}:
        repaired["confidence"] = "low"
    if len(repaired.get("summary", "")) < 20:
        repaired["summary"] = "Partial third-party signal data available — summary incomplete."
    repaired["competitor_name"] = competitor_name
    repaired["cluster_id"] = cluster_id
    try:
        return ThirdPartySignals(**repaired)
    except Exception as e:
        print(f"[web_intelligence] repair failed: {e}")
        return None


def _fallback(competitor_name: str, cluster_id: str) -> ThirdPartySignals:
    """Return the canonical 'evidence unavailable' ThirdPartySignals stamped with competitor + cluster ids.
    Used when SERP is empty, both Grok calls fail, or validation still rejects the merged payload."""
    return ThirdPartySignals(
        competitor_name=competitor_name,
        cluster_id=cluster_id,
        **_FALLBACK_SIGNALS,
    )
