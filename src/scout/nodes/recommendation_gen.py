# recommendation_gen.py — LangGraph node: synthesize a Recommendation per trigger from all three investigation sources.
# Purpose: Assembles an evidence block (SOV + website + third-party + AI citation) and asks Gemini for a typed Recommendation.
# Scope: Evidence truncation, timeline computation, validation+repair, fallback, type/priority derivation from shift_type.
# Consumers: scout/graph.py wires this after investigation nodes; report_generation consumes the recommendations list.
import json
import uuid

from scout.config import get_config
from scout.keys import make_cluster_key, make_trigger_key
from scout.llm import call_synthesis, load_prompt
from scout.models.recommendation import Recommendation
from scout.priority import score_signal
from scout.state import ScoutState
from scout.targets import apply_targets, owned_domains
from scout.utils import is_transient

_TYPE_MAP = {
    "displacement": "defensive",
    "gain": "defensive",
    "new_entrant": "offensive",
    "loss": "offensive",
    "blog_detected": "defensive",
}


def _timeline(confidence: str, probable_cause: str, shift_type: str) -> tuple[str, int]:
    """Single source of truth for the expected-response window: returns (prose, window_weeks).
    window_weeks is the conservative upper bound outcome_measure persists/measures against."""
    cause_lower = (probable_cause or "").lower()
    if confidence == "unknown":
        return "monitoring next cycle — no timeline until cause is identified", 6
    if shift_type == "loss":
        return "act within 2 weeks while competitor is weakened", 2
    if "re-evaluation" in cause_lower or "no new external" in cause_lower:
        return "4-6 weeks (AI re-indexing cycle)", 6
    has_website_cause = any(
        w in cause_lower
        for w in ["page", "schema", "content", "published", "launched", "faq", "case study"]
    )
    has_review_press_cause = any(
        w in cause_lower
        for w in ["review", "press", "publication", "directory", "linkedin", "award"]
    )
    if has_website_cause and confidence in ("high", "medium"):
        return "2-4 weeks post-implementation", 4
    if has_review_press_cause:
        return "4-8 weeks (third-party signal propagation)", 8
    if confidence == "low":
        return "4-8 weeks — low confidence, monitor closely", 8
    return "3-5 weeks", 5

_PRIORITY_MAP = {
    "displacement": "urgent",
    "gain": "standard",
    "new_entrant": "standard",
    "loss": "opportunistic",
    "blog_detected": "standard",
}


def _use_deep(deep_enabled: bool, verdict) -> bool:
    """Deep synthesis only earns its 40K-token budget on graduated clusters — news_mode clusters have no history."""
    return deep_enabled and getattr(verdict, "graduation_regime", "news_mode") == "graduated"


def recommendation_generation(state: ScoutState) -> dict:
    """LangGraph node: iterate triggers, assemble evidence blocks, call Gemini, and return validated Recommendations.
    Returns {'recommendations': [Recommendation, ...]}; falls back to safe defaults when evidence is missing."""
    cfg = get_config()
    deep = cfg.deep_recommendation_enabled
    system_prompt = load_prompt("scout-recommendation-generation")
    deep_system_prompt = load_prompt("scout-recommendation-generation-deep") if deep else None
    triggers = state["investigation_triggers"]
    website_changes = state.get("website_changes", {})
    third_party_signals = state.get("third_party_signals", {})
    ai_citation_changes = state.get("ai_citation_changes", {})
    client_readiness = state.get("client_readiness", {})
    recommendations: list[Recommendation] = []

    # Carries company_domain / company_website — the only authority on which pages a recommendation
    # is allowed to target. Without it every page the LLM proposes is rejected as unowned.
    client_by_id: dict[str, dict] = {
        str(c.get("client_id")): c for c in (state.get("clients") or []) if c.get("client_id")
    }

    client_sov_lookup: dict[tuple[str, str], float] = {}
    for rec in state.get("sov_tracking_records", []):
        if (
            hasattr(rec, "client_id")
            and hasattr(rec, "cluster_id")
            and hasattr(rec, "client_sov_this_week")
            and rec.client_sov_this_week is not None
        ):
            client_sov_lookup.setdefault(
                (rec.client_id, rec.cluster_id), rec.client_sov_this_week
            )

    client_financials_lookup: dict[str, dict] = {}
    if get_config().geo_financials_enabled:
        try:
            from scout.db.client_context import get_client_financials
            from scout.db.supabase_client import get_sed_client
            sb = get_sed_client()
            for t in triggers:
                if t.client_id not in client_financials_lookup:
                    client_financials_lookup[t.client_id] = get_client_financials(sb, t.client_id)
        except Exception as e:
            print(f"[recommendation_gen] financials lookup unavailable: {e}")

    client_gaps_lookup: dict[str, list] = {}
    client_profile_lookup: dict[str, dict] = {}
    if getattr(cfg, "client_gap_recommendations_enabled", False):
        try:
            from scout.db.client_context import get_client_gaps, get_client_profile
            from scout.db.supabase_client import get_sed_client
            sb_gaps = get_sed_client()
            for t in triggers:
                if t.client_id not in client_gaps_lookup:
                    client_gaps_lookup[t.client_id] = get_client_gaps(sb_gaps, t.client_id)
                    client_profile_lookup[t.client_id] = get_client_profile(sb_gaps, t.client_id)
        except Exception as e:
            print(f"[recommendation_gen] client gaps lookup unavailable: {e}")

    client_revenue_bundle: dict[str, dict] = {}
    if cfg.revenue_layer_enabled:
        try:
            from scout.db import revenue_context as rvx
            from scout.db.supabase_client import get_sed_client
            sb_rev = get_sed_client()
            for t in triggers:
                if t.client_id not in client_revenue_bundle:
                    handles = rvx.get_client_revenue_handles(sb_rev, t.client_id)
                    gsc = rvx.get_gsc_demand(sb_rev, handles.get("gsc_site_url", "")) if cfg.geo_gsc_enabled else []
                    ga4 = rvx.get_ga4_revenue(sb_rev, handles.get("ga4_property_id", "")) if cfg.geo_ga4_enabled else []
                    demand_by_cluster = rvx.map_gsc_queries_to_clusters(sb_rev, t.client_id, gsc)
                    client_revenue_bundle[t.client_id] = {
                        "demand_by_cluster": demand_by_cluster, "ga4": ga4,
                        "fx_rates": {},
                    }
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "[recommendation_gen] revenue reads FAILED (revenue will be missing for ALL clients this run): %s", e,
                exc_info=True,
            )

    verdicts = state.get("cluster_verdicts", []) or []
    trigger_by_key = {
        make_trigger_key(t.client_id, t.competitor_name, t.cluster_id): t for t in triggers
    }

    # R5-4: load the measured cause-class recovery priors once per run (only when feedback is enabled).
    cause_accuracy = None
    if get_config().calibration_feedback_enabled:
        try:
            from scout.db.calibration import cause_type_accuracy
            from scout.db.supabase_client import get_sed_client
            cause_accuracy = cause_type_accuracy(get_sed_client())
        except Exception as e:
            print(f"[recommendation_gen] calibration feedback unavailable: {e}")

    for verdict in verdicts:
        # R4-3: collapse a NOISE verdict to digest-only when the cluster has GRADUATED (regardless of the
        # global noise_full_report); a non-graduated NOISE cluster keeps the verbose report. The global
        # noise_full_report flag survives only as a debug/emergency force-on override for non-graduated clusters.
        if verdict.noise and (getattr(verdict, "graduation_regime", "news_mode") == "graduated" or not get_config().noise_full_report):
            continue
        key = make_trigger_key(verdict.client_id, verdict.primary_competitor, verdict.cluster_id)
        trigger = trigger_by_key.get(key)
        if trigger is None:
            continue
        print(f"[recommendation_gen] Processing {key} (field of {len(verdict.field)})")

        cr = client_readiness.get(trigger.client_id)
        fin = client_financials_lookup.get(trigger.client_id)

        wc = website_changes.get(key)
        tps = third_party_signals.get(key)
        aic = ai_citation_changes.get(key)
        # R3-4: the crude substring all_missing heuristic is gone. Thin cycles route through the normal
        # LLM path with R3-3's structured abstention aggregation (cap confidence, name abstained sources);
        # _safe_fallback_recommendation fires only on a real API/validation failure inside _call_with_fallback.
        abstained = _abstained_sources(wc, tps, aic)

        expected_type = _TYPE_MAP.get(trigger.shift_type, "defensive")
        expected_priority = _priority_for_trigger(trigger)

        client_sov_now = client_sov_lookup.get((trigger.client_id, trigger.cluster_id))
        client_record = client_by_id.get(trigger.client_id, {})

        _ground_events(trigger, get_config())
        use_deep = _use_deep(deep, verdict)
        if use_deep:
            history = state.get("historical_context", {}).get(
                make_cluster_key(verdict.client_id, verdict.cluster_id)
            )
            evidence_block = _build_deep_evidence_block(
                verdict, trigger, website_changes, third_party_signals,
                ai_citation_changes, client_sov_now, fin, cr, history, client_record,
            )
        else:
            evidence_block = _build_evidence_block(
                trigger, wc, tps, aic, client_sov_now, fin, cr, client_record
            )
            evidence_block += _field_evidence_block(verdict)
        if abstained:
            if use_deep:
                evidence_block += (
                    "\n\nABSTAINED SOURCES this week (looked, found nothing above the evidence floor — "
                    f"name them, but weigh the field + history for the cause): {', '.join(abstained)}"
                )
            else:
                evidence_block += (
                    "\n\nABSTAINED SOURCES (looked, found nothing above the evidence floor — do NOT "
                    f"synthesize a cause from these; name them in gap_analysis): {', '.join(abstained)}"
                )

        user_message = (
            f"Generate a recommendation for the following competitive intelligence investigation.\n\n"
            f"{evidence_block}\n\n"
            f"Return a JSON object matching this schema exactly:\n"
            f"{{\n"
            f'  "investigation_id": "{uuid.uuid4()!s}",\n'
            f'  "competitor_name": "{trigger.competitor_name}",\n'
            f'  "cluster_id": "{trigger.cluster_id}",\n'
            f'  "cluster_label": "{trigger.cluster_label}",\n'
            f'  "shift_type": "{trigger.shift_type}",\n'
            f'  "type": "{expected_type}",\n'
            f'  "priority": "{expected_priority}",\n'
            f'  "probable_cause": "string (min 10 chars)",\n'
            f'  "confidence": "high|medium|low|unknown",\n'
            f'  "gap_analysis": "string (min 20 chars)",\n'
            f'  "action_bullets": ["3 to 5 strings, each starting with an action verb"],\n'
            f'  "target_pages": ["absolute URLs on the CLIENT\'S OWN domain that this action '
            f'changes or creates; [] if the action is not page-level. Never a competitor or '
            f'third-party URL — those are rejected."],\n'
            f'  "target_queries": ["the real search queries this action should improve; '
            f'short buyer-style queries, not the long monitoring prompt"],\n'
            f'  "action_type": "one of content_update|new_page|schema|ai_access|third_party|'
            f'measurement|other",\n'
            f'  "expected_leading_outcome": "the first measurable signal if this works '
            f'(e.g. AI citations, GSC impressions/clicks)",\n'
            f'  "expected_business_outcome": "the commercial result it should lead to '
            f'(e.g. qualified visits, demo requests)",\n'
            f'  "summary": "string",\n'
            f'  "slack_report": "brief slack-formatted summary string"\n'
            f"}}"
        )

        result = _call_with_fallback(
            deep_system_prompt if use_deep else system_prompt,
            user_message,
            trigger,
            expected_type,
            expected_priority,
            key,
            abstained,
            model=(cfg.deep_recommendation_model or None) if use_deep else None,
            max_tokens=cfg.deep_recommendation_max_tokens if use_deep else None,
        )
        # R5-4: calibration-driven confidence feedback (OFF by default; no-op when disabled or below volume).
        # Runs before the abstention cap so abstention honesty always wins over the measured prior.
        result.confidence = _apply_calibration_feedback(result, cause_accuracy)
        # R3-3: standard path only — with >= min_abstained_for_gap sources abstained, name them in
        # gap_analysis and cap confidence at low. The deep path names abstained sources via the prompt
        # and judges confidence on the full field + multi-week history instead of a hard cap.
        if not use_deep and len(abstained) >= get_config().min_abstained_for_gap:
            if result.confidence in ("high", "medium"):
                result.confidence = "low"
            names = ", ".join(abstained)
            if names not in (result.gap_analysis or ""):
                result.gap_analysis = (
                    (result.gap_analysis or "").rstrip()
                    + f" Abstained evidence sources (no signal above floor): {names}."
                ).strip()
        result.timeline, result.window_weeks = _timeline(result.confidence, result.probable_cause, result.shift_type)

        # Validate the LLM's claimed targets BEFORE revenue: a page-level revenue category can only
        # be earned through a client-owned page, so targets must be settled first.
        _apply_validated_targets(result, client_record)

        rev = _cluster_revenue(
            cfg, verdict, trigger, client_revenue_bundle,
            client_sov_now=client_sov_now, financials=fin, target_pages=result.target_pages,
        )
        if rev is not None:
            verdict.revenue_category = rev["revenue_category"]

        _apply_priority_score(
            result, cfg, trigger,
            client_sov_now=client_sov_now,
            demand=client_revenue_bundle.get(trigger.client_id, {})
            .get("demand_by_cluster", {}).get(verdict.cluster_id, {}),
            readiness=cr,
        )

        recommendations.append(_stamp_context(result, cr, fin, wc, rev, client_gaps_lookup.get(trigger.client_id),
                                              client_profile_lookup.get(trigger.client_id)))

    # Highest commercial priority first. Ordering is by an auditable component vector, never by a
    # dollar figure — the old revenue ordering ranked clusters by how small the client's share was.
    recommendations.sort(key=lambda r: -r.priority_score)
    return {"recommendations": recommendations}


def _apply_validated_targets(rec: Recommendation, client: dict) -> None:
    """Validate the LLM's claimed targets against the client's own domains, in place.

    Anything not on a client-owned domain is dropped and recorded in target_rejections. A
    recommendation that ends up `unmapped` is still publishable — it simply cannot be measured, and
    outcome measurement will skip it rather than measure something unrelated."""
    validated = apply_targets(
        {
            "target_pages": rec.target_pages,
            "target_queries": rec.target_queries,
            "action_type": rec.action_type,
        },
        client,
    )
    rec.target_pages = validated["target_pages"]
    rec.target_queries = validated["target_queries"]
    rec.action_type = validated["action_type"]
    rec.mapping_confidence = validated["mapping_confidence"]
    rec.target_rejections = validated["target_rejections"]
    if validated["target_rejections"]:
        dropped = ", ".join(
            f"{r['url']} ({r['reason']})" for r in validated["target_rejections"][:5]
        )
        print(f"[recommendation_gen] dropped unowned/invalid target pages: {dropped}")


def _apply_priority_score(rec: Recommendation, cfg, trigger, *, client_sov_now, demand,
                          readiness) -> None:
    """Score how much this signal deserves action, in place.

    Runs after target validation so `actionability` reflects a real, validated page rather than one
    the LLM merely proposed. Components with no input are excluded rather than scored zero — see
    scout/priority.py."""
    score = score_signal(
        shift_magnitude=getattr(trigger, "shift_magnitude", None),
        client_sov_pp=client_sov_now,
        search_impressions=(demand or {}).get("impressions"),
        mapping_confidence=rec.mapping_confidence,
        evidence_confidence=rec.confidence,
        action_type=rec.action_type,
        client_readiness=readiness,
        weights=getattr(cfg, "priority_weights", None) or None,
    )
    rec.priority_score = score.score
    rec.priority_band = score.band
    rec.priority_components = score.to_dict()


def _cluster_revenue(cfg, verdict, trigger, client_revenue_bundle, *, client_sov_now,
                     financials, target_pages) -> dict | None:
    """Grade this cluster's revenue evidence, scoped to the recommendation's validated pages.

    Returns None when the revenue layer is off. With no validated target pages there is no
    page-level linkage, so the category resolves to `unavailable` — correct, not a failure."""
    if not cfg.revenue_layer_enabled:
        return None
    from scout import revenue as R
    from scout.db.revenue_context import normalize_url

    bundle = client_revenue_bundle.get(trigger.client_id, {})
    sov_for_rev = {
        "client_sov_pp": client_sov_now,
        "primary_competitor_sov_pp": next(
            (f.get("delta_pp") for f in (verdict.field or [])
             if f.get("competitor") == verdict.primary_competitor),
            None,
        ),
    }
    return R.compute_cluster_revenue(
        demand=bundle.get("demand_by_cluster", {}).get(verdict.cluster_id, {}),
        financials=financials or {},
        sov=sov_for_rev,
        ga4=bundle.get("ga4"),
        fx_rates=bundle.get("fx_rates"),
        target_landing_pages={normalize_url(p) for p in target_pages} or None,
        normalizer=normalize_url,
        capture_fraction=(cfg.modeled_scenario_capture_fraction
                          if cfg.modeled_scenario_enabled else None),
    )


def _priority_for_trigger(trigger) -> str:
    """Return the priority to apply: prefer the trigger's own investigation_priority when valid, else map from shift_type.
    Ensures recommendations always carry one of urgent/standard/opportunistic regardless of upstream quirks."""
    if trigger.investigation_priority in ("urgent", "standard", "opportunistic"):
        return trigger.investigation_priority
    return _PRIORITY_MAP.get(trigger.shift_type, "standard")


def _stamp_context(rec: Recommendation, cr, fin: dict | None, wc=None, rev: dict | None = None,
                   stored_gaps: list | None = None, profile_row: dict | None = None) -> Recommendation:
    """Stamp carried GEO context (client readiness + gaps + revenue + competitor AI-access) onto a Recommendation in place, then return it.
    Keeps the structured signal on the object so report_gen surfaces it deterministically, not relying on the LLM."""
    rec.client_readiness = cr.model_dump() if cr else {}
    if stored_gaps is not None:   # gap flag on (lookup ran): merge stored recon gaps + report-time readiness gaps (competitive stays out of report path)
        from scout.builders.gap_recommendations import rank_gap_dicts, readiness_gaps
        fresh = [g.model_dump() for g in readiness_gaps(cr, profile_row or {})] if cr else []
        rec.client_gaps = rank_gap_dicts(stored_gaps + fresh)
    rec.revenue_context = fin or {}
    rec.co_mention_density = {}
    rec.competitor_ai_access = (getattr(wc, "ai_access", None) or {}) if wc else {}
    rec.slack_report = _normalize_slack_type(rec.slack_report, rec.type, rec.confidence)
    if rev is not None:
        rec.revenue_category = rev.get("revenue_category", "unavailable")
        rec.revenue_value_usd = rev.get("revenue_value_usd")
        rec.revenue_currency = rev.get("revenue_currency", "USD")
        rec.revenue_limitations = rev.get("revenue_limitations", [])
        rec.revenue_inputs = rev.get("revenue_inputs", {})
    return rec


def _normalize_slack_type(slack_report: str, rec_type: str, confidence: str) -> str:
    """Force the Slack report's Type/confidence footer to match the structured rec_type, not the LLM's free text.
    Strips any model-authored 'Type:' line and appends one deterministic footer so the two can never disagree."""
    lines = [ln for ln in (slack_report or "").splitlines() if not ln.strip().lower().startswith("type:")]
    footer = f"Type: {(rec_type or '').capitalize()} | Evidence confidence: {confidence}"
    return ("\n".join(lines).rstrip() + "\n\n" + footer).strip()


def _field_evidence_block(verdict) -> str:
    """Render a short FIELD section listing the other competitors on this client-cluster (secondaries).
    Keeps the recommendation focused on the primary while noting the full competitive field by delta."""
    secondaries = [f for f in (verdict.field or []) if f.get("competitor") != verdict.primary_competitor]
    if not secondaries:
        return ""
    lines = ["", "FIELD (other competitors on this client-cluster — secondary; summarize, do not over-weight):"]
    for f in secondaries:
        lines.append(f"  {f.get('competitor')}: {f.get('delta_pp')}pp")
    return "\n".join(lines)


def _abstained_sources(wc, tps, aic) -> list[str]:
    """Return the names of investigation sources that abstained (looked, found nothing above the evidence floor).
    Reads the typed `abstained` flag set by the investigation nodes (R3-3), not the old substring/confidence heuristic."""
    out = []
    if getattr(wc, "abstained", False):
        out.append("website_changes")
    if getattr(tps, "abstained", False):
        out.append("third_party_signals")
    if getattr(aic, "abstained", False):
        out.append("ai_citation_changes")
    return out


def _ground_events(trigger, cfg) -> None:
    """When evidence_grounding_enabled, top up the trigger's evidence with real, cited competitor announcements
    (Bright Data SERP) so a named cause can be grounded. Best-effort; never raises."""
    if not getattr(cfg, "evidence_grounding_enabled", False):
        return
    try:
        from scout.integrations.bright_data import find_competitor_events
        for e in find_competitor_events(trigger.competitor_name, trigger.cluster_label, trigger.shift_type,
                                        max_events=getattr(cfg, "grounding_serp_max", 3)):
            trigger.blog_evidence.append({"title": e.get("event"), "url": e.get("source_url"),
                                          "source": "bright_data_serp", "excerpt": e.get("excerpt")})
    except Exception as e:
        print(f"[recommendation_gen] event grounding failed for {trigger.competitor_name}: {e}")


_CONF_ORDER = ["unknown", "low", "medium", "high"]


def _nudge_confidence(current: str, recovery_rate: float) -> str:
    """Step confidence down one tier when the measured recovery prior is weak (<0.4), up one tier when strong (>0.7).
    Stays within the {unknown,low,medium,high} enum and never moves more than one step (deterministic, no LLM)."""
    if current not in _CONF_ORDER:
        return current
    i = _CONF_ORDER.index(current)
    if recovery_rate < 0.4 and i > 0:
        return _CONF_ORDER[i - 1]
    if recovery_rate > 0.7 and i < len(_CONF_ORDER) - 1:
        return _CONF_ORDER[i + 1]
    return current


def _apply_calibration_feedback(result, cause_accuracy: dict | None) -> str:
    """Deterministically nudge a recommendation's confidence toward the measured cause-class recovery prior, enum-clamped.
    No-op (returns the original confidence) when feedback is disabled, the cause class is unknown, or volume is below calibration_min_samples (R5-4)."""
    cfg = get_config()
    if not cfg.calibration_feedback_enabled or not cause_accuracy:
        return result.confidence
    stats = cause_accuracy.get(getattr(result, "type", None))
    if not stats or stats.get("n", 0) < cfg.calibration_min_samples:
        return result.confidence
    rate = stats.get("recovery_rate")
    if rate is None:
        return result.confidence
    return _nudge_confidence(result.confidence, rate)


def _client_site_block(client: dict | None) -> list[str]:
    """Tell the model which domain it may target. Without this it invents plausible third-party URLs,
    which the validator then strips — leaving a recommendation that cannot be measured."""
    owned = sorted(owned_domains(client or {}))
    if not owned:
        return [
            "CLIENT SITE: unknown — no registered domain for this client.",
            "  Leave target_pages empty; a page you cannot verify as the client's own will be rejected.",
            "",
        ]
    website = ((client or {}).get("company_website") or "").strip()
    return [
        "CLIENT SITE (target_pages MUST be on these domains — anything else is dropped):",
        f"  owned_domains: {', '.join(owned)}",
        f"  website: {website or 'unknown'}",
        "  Competitor and third-party URLs are evidence, never targets.",
        "",
    ]


def _build_evidence_block(trigger, wc, tps, aic, client_sov_now: float | None = None,
                          fin: dict | None = None, cr=None, client: dict | None = None) -> str:
    """Render a multi-line prompt block summarizing trigger + client state + the FULL evidence from all three sources.
    Adds optional REVENUE CONTEXT and CLIENT READINESS blocks; R0-2 passes evidence uncut (no per-source budget or summarization)."""
    lines = [
        *_client_site_block(client),
        "TRIGGER DETAILS:",
        f"  competitor: {trigger.competitor_name}",
        f"  cluster: {trigger.cluster_label} ({trigger.cluster_id})",
        f"  shift_type: {trigger.shift_type}",
        f"  shift_magnitude: {trigger.shift_magnitude}pp",
        f"  triage_reason: {trigger.triage_reason}",
        f"  correlated_displacement: {trigger.correlated_displacement}",
        "",
        "CLIENT STATE:",
        f"  client_sov_this_week: {client_sov_now}pp" if client_sov_now is not None else "  client_sov_this_week: unavailable",
        f"  client_sov_change_vs_4w_avg: {trigger.client_sov_change:.2f}pp" if trigger.client_sov_change is not None else "  client_sov_change_vs_4w_avg: unavailable",
        "",
    ]
    ev = getattr(trigger, "blog_evidence", None)
    if ev:
        lines.append("COLLECTED EVIDENCE — competitor posts/announcements. Name a SPECIFIC event only if it "
                     "appears below, and cite its source URL. If none fits, give a plausible general cause from "
                     "the observable signals — never assert an unverified specific:")
        for e in ev[:8]:
            title = (e.get("title") or e.get("event") or "").strip()
            url = (e.get("url") or e.get("source_url") or "").strip()
            if title or url:
                lines.append(f"  - {title} [{url}]")
        lines.append("")
    if fin and fin.get("average_order_value"):
        lines += [
            "REVENUE CONTEXT (for framing impact magnitude — not a claim to repeat verbatim):",
            f"  avg_order_value: {fin.get('average_order_value')} {fin.get('currency', '')}".rstrip(),
            f"  conversion_rate: {fin.get('conversion_rate')}  estimated_ctr: {fin.get('estimated_ctr')}",
            "",
        ]
    if cr is not None and getattr(cr, "confidence", "none") != "none":
        lines += [
            "CLIENT READINESS (the client's OWN site — cite concrete gaps in recommended actions):",
            f"  ai_access_files: llms.txt={cr.llms_txt_present} ai_bots={cr.ai_bots_present} robots={cr.robots_present}",
            f"  schema_present: {', '.join(cr.schema_types_present) or 'none'}",
            f"  schema_missing: {', '.join(cr.schema_types_missing) or 'none'}",
        ]
        comp_ai = (getattr(wc, "ai_access", None) or {}) if wc else {}
        if comp_ai:
            lines.append(
                f"  ai_access_comparison: competitor {trigger.competitor_name} "
                f"llms.txt={comp_ai.get('llms_txt')} ai_bots={comp_ai.get('ai_bots')} robots={comp_ai.get('robots_txt')} "
                f"(client llms.txt={cr.llms_txt_present})"
            )
        lines.append("")
    if wc:
        wc_dict = wc.model_dump() if hasattr(wc, "model_dump") else {}
        wc_full = json.dumps(wc_dict, default=str)
        lines.append(f"WEBSITE CHANGES (confidence={wc.confidence}):")
        lines.append(f"  {wc_full}")
    if tps:
        tps_dict = tps.model_dump() if hasattr(tps, "model_dump") else {}
        tps_full = json.dumps(tps_dict, default=str)
        lines.append(f"THIRD-PARTY SIGNALS (confidence={tps.confidence}):")
        lines.append(f"  {tps_full}")
    if aic:
        aic_dict = aic.model_dump() if hasattr(aic, "model_dump") else {}
        delta_full = str(aic_dict.get("delta_summary", ""))
        lines.append("AI CITATION CHANGES (delta_summary):")
        lines.append(f"  {delta_full}")
    return "\n".join(lines)


def _build_deep_evidence_block(verdict, trigger, website_changes, third_party_signals,
                               ai_citation_changes, client_sov_now, fin, cr, history,
                               client: dict | None = None) -> str:
    # Whole-field, multi-week evidence: client/readiness header + full per-competitor current evidence
    # (full AI-citation, not just delta_summary) + each competitor's trajectory + prior recs/outcomes.
    lines = [_build_evidence_block(trigger, None, None, None, client_sov_now, fin, cr, client)]
    lines.append("\nFIELD — every competitor on this cluster this week (full evidence):")
    for f in (verdict.field or []):
        comp = f.get("competitor")
        k = make_trigger_key(verdict.client_id, comp, verdict.cluster_id)
        wc = website_changes.get(k)
        tps = third_party_signals.get(k)
        aic = ai_citation_changes.get(k)
        lines.append(f"\n- {comp} ({f.get('delta_pp')}pp):")
        if wc:
            lines.append(f"  website_changes: {json.dumps(wc.model_dump(), default=str)}")
        if tps:
            lines.append(f"  third_party_signals: {json.dumps(tps.model_dump(), default=str)}")
        if aic:
            lines.append(f"  ai_citation_changes: {json.dumps(aic.model_dump(), default=str)}")
        if not (wc or tps or aic):
            lines.append("  (no investigation evidence above floor this week)")
    if history is not None:
        lines.append("\nHISTORY — multi-week trajectory per competitor:")
        for ch in history.competitors:
            lines.append(f"\n- {ch.competitor_name}:")
            lines.append(f"  sov_track: {json.dumps(ch.sov_track, default=str)}")
            if ch.investigations:
                lines.append(f"  investigations: {json.dumps(ch.investigations, default=str)}")
        if history.prior_recommendations:
            lines.append(f"\nPRIOR RECOMMENDATIONS (this cluster): {json.dumps(history.prior_recommendations, default=str)}")
        if history.outcomes:
            lines.append(f"MEASURED OUTCOMES (this cluster): {json.dumps(history.outcomes, default=str)}")
        if history.ai_citation_weeks:
            lines.append(f"AI CITATION WEEKS (this cluster): {json.dumps(history.ai_citation_weeks, default=str)}")
    return "\n".join(lines)



def _call_with_fallback(
    system_prompt: str,
    user_message: str,
    trigger,
    expected_type: str,
    expected_priority: str,
    trigger_key: str,
    abstained_sources: list[str] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Recommendation:
    """Invoke Gemini with one transient-retry and coerce critical fields from the trigger before validation.
    Falls back to _safe_fallback_recommendation (naming any abstained sources) only on validation failure or a non-transient error."""
    for attempt in range(2):
        try:
            raw = call_synthesis(
                system_prompt=system_prompt,
                user_message=user_message,
                expect_json=True,
                node_name="recommendation_generation",
                trigger_key=trigger_key,
                model=model,
                max_tokens=max_tokens,
            )
            raw["client_name"] = trigger.client_name
            raw["competitor_name"] = trigger.competitor_name
            raw["cluster_id"] = trigger.cluster_id
            raw["cluster_label"] = trigger.cluster_label
            raw["shift_type"] = trigger.shift_type
            raw["type"] = expected_type
            raw["priority"] = expected_priority
            raw.setdefault("investigation_id", str(uuid.uuid4()))
            validated = _validate_and_repair(raw, trigger, expected_type, expected_priority)
            if validated:
                return validated
            print(f"[recommendation_gen] output validation failed for {trigger_key}, using fallback")
            return _safe_fallback_recommendation(trigger, expected_type, expected_priority, abstained_sources)
        except Exception as e:
            if is_transient(e) and attempt == 0:
                continue
            print(f"[recommendation_gen] API error for {trigger_key}: {e}")
            return _safe_fallback_recommendation(trigger, expected_type, expected_priority, abstained_sources)
    return _safe_fallback_recommendation(trigger, expected_type, expected_priority, abstained_sources)


def _validate_and_repair(
    raw: dict, trigger, expected_type: str, expected_priority: str
) -> Recommendation | None:
    """Validate a raw recommendation dict; if invalid, patch action_bullet cardinality and required text fields, then retry.
    Returns None when both attempts fail so the caller knows to fall back to the safe default."""
    grounded = dict(raw)
    # Identity comes from the resolved trigger, never from LLM output.
    grounded["client_id"] = trigger.client_id
    grounded["client_name"] = trigger.client_name
    grounded["competitor_name"] = trigger.competitor_name
    grounded["cluster_id"] = trigger.cluster_id
    grounded["cluster_label"] = trigger.cluster_label
    grounded["shift_type"] = trigger.shift_type
    try:
        rec = Recommendation(**grounded)
        bullets = rec.action_bullets
        if len(bullets) > 5:
            grounded["action_bullets"] = bullets[:5]
            rec = Recommendation(**grounded)
        return rec
    except Exception:
        pass
    repaired = dict(grounded)
    repaired.setdefault("investigation_id", str(uuid.uuid4()))
    repaired.setdefault("probable_cause", f"{trigger.shift_type} detected — investigation in progress.")
    repaired.setdefault("confidence", "low")
    repaired.setdefault("gap_analysis", "Evidence incomplete — further investigation required.")
    repaired.setdefault("summary", f"{trigger.competitor_name} {trigger.shift_type} on {trigger.cluster_label}.")
    repaired.setdefault("slack_report", repaired["summary"])
    bullets = repaired.get("action_bullets", [])
    if len(bullets) > 5:
        repaired["action_bullets"] = bullets[:5]
    if len(bullets) < 3:
        repaired["action_bullets"] = [
            f"Investigate {trigger.competitor_name} content changes on {trigger.cluster_label}.",
            "Monitor SOV weekly and compare to baseline.",
            "Brief client with findings within 5 business days.",
        ]
    repaired["type"] = expected_type
    repaired["priority"] = expected_priority
    try:
        return Recommendation(**repaired)
    except Exception as e:
        print(f"[recommendation_gen] repair failed: {e}")
        return None


def _safe_fallback_recommendation(trigger, expected_type: str, expected_priority: str, abstained_sources: list[str] | None = None) -> Recommendation:
    """Build a minimally-valid 'unknown'-confidence Recommendation used ONLY when the LLM path fails (API/validation error).
    When abstained_sources are known, gap_analysis/probable_cause name them honestly; otherwise it states the generation failure (R3-4)."""
    if abstained_sources:
        names = ", ".join(abstained_sources)
        probable_cause = f"{trigger.shift_type.replace('_', ' ').title()} detected for {trigger.competitor_name}; evidence sources abstained: {names}."
        gap_analysis = f"Evidence sources abstained (looked, found nothing above floor): {names}. No cause synthesized — monitoring next cycle."
    else:
        probable_cause = f"{trigger.shift_type.replace('_', ' ').title()} detected for {trigger.competitor_name}."
        gap_analysis = "Recommendation generation failed (API/validation) — safe fallback emitted; re-investigate next cycle."
    return Recommendation(
        investigation_id=str(uuid.uuid4()),
        client_id=trigger.client_id,
        client_name=trigger.client_name,
        competitor_name=trigger.competitor_name,
        cluster_id=trigger.cluster_id,
        cluster_label=trigger.cluster_label,
        shift_type=trigger.shift_type,
        type=expected_type,
        priority=expected_priority,
        probable_cause=probable_cause,
        confidence="unknown",
        gap_analysis=gap_analysis,
        action_bullets=[
            f"Audit {trigger.competitor_name} content on {trigger.cluster_label} cluster.",
            "Monitor AI platform citation frequency over next 2 weeks.",
            "Brief client on competitive shift and expected timeline for response.",
        ],
        summary=f"{trigger.competitor_name} triggered a {trigger.shift_type} alert on {trigger.cluster_label}.",
        slack_report=f"[{trigger.investigation_priority.upper()}] {trigger.competitor_name} — {trigger.shift_type} on {trigger.cluster_label}.",
    )
