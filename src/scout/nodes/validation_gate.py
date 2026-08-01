# validation_gate.py — LangGraph node: pre-publish data-integrity + numeric-provenance gate (mark-and-write).
# Purpose: Scan each (recommendation, internal report, client summary) for token/unit/empty/type defects, and (R2) check every numeral traces to per-verdict structured evidence, before delivery.
# Scope: Deterministic checks only; cheap in-place repairs (signed-% -> pp, Type line) and quarantine for ambiguous defects. Also holds the four evidence dicts + cluster_verdicts + sov_tracking_records for provenance. No LLM, no I/O.
# Consumers: scout/graph.py runs it between report_generation and slack_delivery; slack_delivery skips quarantined recs.
import re

from scout.config import get_config
from scout.provenance import (
    build_entity_allowlist,
    build_number_allowlist,
    extract_claim_entities,
    extract_numerals,
    match_allowlisted,
    qualify_unbacked_claims,
    rewrite_unbacked_client,
    unbacked_entities,
)
from scout.state import ScoutState

_TOKEN_RE = re.compile(r"\[[^\[\]]{0,60}\]")                 # unresolved [ ... ] placeholder tokens
_SIGNED_PCT_RE = re.compile(r"([+\-]\s?\d+(?:\.\d+)?)\s?%")  # share deltas written as +16.75% / -6.00%
_CONF_ENUM = {"high", "medium", "low", "unknown"}


def _repair_signed_pct(text: str) -> tuple[str, bool]:
    """Rewrite signed-percent share deltas (e.g. '+16.75%') as percentage points ('+16.75pp').
    Returns (text, changed); leaves unsigned percentages (conversion rates etc.) untouched."""
    if not text:
        return text, False
    new = _SIGNED_PCT_RE.sub(lambda mt: f"{mt.group(1).replace(' ', '')}pp", text)
    return new, (new != text)


def _has_token(text: str) -> bool:
    """Return True when text carries an unresolved square-bracket placeholder token.
    Ignores empty/None text so absent fields never trip the check."""
    return bool(text) and bool(_TOKEN_RE.search(text))


def _stamp(obj, status: str, notes: list[str]) -> None:
    """Set validation_status + validation_notes on an artifact model in place.
    Shared across the rec/report/summary triple so their verdicts stay consistent."""
    obj.validation_status = status
    obj.validation_notes = list(notes)


def validation_gate(state: ScoutState) -> dict:
    """LangGraph node: validate every artifact triple, repairing safe defects and quarantining ambiguous ones.
    Returns updated recommendations/internal_reports/client_summaries with validation_status + notes stamped."""
    recs = state.get("recommendations", []) or []
    reports = state.get("internal_reports", []) or []
    summaries = state.get("client_summaries", []) or []

    # R2-1: hold the structured evidence the numbers must trace to (still populated when the gate runs —
    # no investigation node clears these). R2-2 builds the per-verdict allowlist from them; R2-3 checks numerals.
    wc_all = state.get("website_changes", {}) or {}
    tps_all = state.get("third_party_signals", {}) or {}
    aic_all = state.get("ai_citation_changes", {}) or {}
    cr_all = state.get("client_readiness", {}) or {}
    verdicts = state.get("cluster_verdicts", []) or []
    sov_records = state.get("sov_tracking_records", []) or []
    # R2-2: map each rec to its verdict by (client_name, cluster_id) — the same key the dup-check uses below.
    verdict_by_ck = {(getattr(v, "client_name", ""), getattr(v, "cluster_id", "")): v for v in verdicts}
    # R2-4: per-node hallucinated-number accumulators (measured in shadow + enforce alike).
    np_total = {"client_summary": 0, "internal_report": 0}
    np_unbacked = {"client_summary": 0, "internal_report": 0}

    # R-CLAIM: named-event provenance — one evidence-entity allowlist (numbers' sibling) + known competitor/client
    # names, so a claim naming a real collected event/product is backed and only invented specifics are flagged.
    def _dump(o):
        return o.model_dump() if hasattr(o, "model_dump") else o
    blog_ev = [getattr(t, "blog_evidence", None) or [] for t in (state.get("investigation_triggers", []) or [])]
    entity_allow = build_entity_allowlist(
        *([_dump(v) for v in wc_all.values()] + [_dump(v) for v in tps_all.values()]
          + [_dump(v) for v in aic_all.values()] + blog_ev))
    known_names: set = set()
    for v in verdicts:
        known_names.add(getattr(v, "primary_competitor", ""))
        known_names.add(getattr(v, "client_name", ""))
        for f in (getattr(v, "field", None) or []):
            known_names.add(f.get("competitor", ""))
    for _r in recs:
        known_names.update([_r.client_name, _r.competitor_name, _r.cluster_label])
    cl_total = {"client_summary": 0, "internal_report": 0}
    cl_unbacked = {"client_summary": 0, "internal_report": 0}

    for i, rec in enumerate(recs):
        notes: list[str] = []
        quarantine = False
        ir = reports[i] if i < len(reports) else None
        cs = summaries[i] if i < len(summaries) else None

        # R2-2: per-verdict number allowlist (every numeral reachable from this verdict's structured evidence).
        # R2-3 checks each artifact numeral against `allow`; an empty allowlist (no verdict) skips the numeric check.
        verdict = verdict_by_ck.get((rec.client_name, rec.cluster_id))
        allow = (
            build_number_allowlist(verdict, wc_all, tps_all, aic_all, cr_all, sov_records,
                                   getattr(rec, "revenue_context", None))
            if verdict is not None else set()
        )

        # 1) Unit repair: signed-% share deltas -> pp (safe, in place).
        fixed_slack, ch1 = _repair_signed_pct(rec.slack_report)
        if ch1:
            rec.slack_report = fixed_slack
            notes.append("repaired: signed % share delta -> pp in slack_report")
        if ir is not None:
            fixed_rt, ch2 = _repair_signed_pct(ir.report_text)
            if ch2:
                ir.report_text = fixed_rt
                notes.append("repaired: signed % share delta -> pp in internal_report")

        # 2) Slack Type line must match structured rec.type (safe, in place).
        if rec.slack_report:
            want = f"Type: {(rec.type or '').capitalize()}"
            fixed_type = re.sub(r"(?im)^Type:\s*\w+", want, rec.slack_report)
            if fixed_type != rec.slack_report:
                rec.slack_report = fixed_type
                notes.append("repaired: slack Type aligned to structured rec.type")

        # 3) Unresolved [ ... ] tokens -> quarantine (ambiguous; never guess the value).
        token_fields = [rec.slack_report, rec.summary, rec.probable_cause, rec.gap_analysis]
        token_fields += list(rec.action_bullets or [])
        token_fields.append(getattr(ir, "report_text", "") if ir is not None else "")
        token_fields.append(getattr(cs, "summary_text", "") if cs is not None else "")
        if any(_has_token(t) for t in token_fields):
            quarantine = True
            notes.append("quarantined: unresolved [placeholder] token in output")

        # 4) Empty client summary / internal report -> quarantine.
        if cs is not None and not (cs.summary_text or "").strip():
            quarantine = True
            notes.append("quarantined: empty client_summary")
        if ir is not None and not (ir.report_text or "").strip():
            quarantine = True
            notes.append("quarantined: empty internal_report")

        # 5) Confidence must be categorical, not numeric.
        if rec.confidence not in _CONF_ENUM:
            quarantine = True
            notes.append(f"quarantined: non-enum confidence '{rec.confidence}'")

        # 6) R2-3: numeric provenance — every numeral must trace to this verdict's evidence allowlist.
        #    Shadow mode (default; formalized in R2-4) records notes only; enforce mode rewrites the client
        #    summary to qualitative and quarantines the triple for an unbacked internal-report number.
        if allow:
            cfg = get_config()
            shadow = getattr(cfg, "numeric_provenance_shadow_mode", True)
            client_enforce = (not shadow) and getattr(cfg, "numeric_provenance_client_enforce", False)
            internal_enforce = (not shadow) and getattr(cfg, "numeric_provenance_internal_enforce", False)
            cs_text = (getattr(cs, "summary_text", "") if cs is not None else "") or ""
            ir_text = (getattr(ir, "report_text", "") if ir is not None else "") or ""
            cs_numerals = extract_numerals(cs_text)
            ir_numerals = extract_numerals(ir_text)
            unbacked_cs = [n for n in cs_numerals if not match_allowlisted(n, allow)]
            unbacked_ir = [n for n in ir_numerals if not match_allowlisted(n, allow)]
            np_total["client_summary"] += len(cs_numerals)
            np_total["internal_report"] += len(ir_numerals)
            np_unbacked["client_summary"] += len(unbacked_cs)
            np_unbacked["internal_report"] += len(unbacked_ir)
            if unbacked_cs:
                if client_enforce:
                    new_cs, n_rw = rewrite_unbacked_client(cs_text, allow)
                    if cs is not None and n_rw:
                        cs.summary_text = new_cs
                    notes.append("repaired: unbacked number rewritten to qualitative in client_summary")
                else:
                    notes.append(f"shadow: unbacked number(s) in client_summary {unbacked_cs}")
            if unbacked_ir:
                if internal_enforce:
                    quarantine = True
                    notes.append("quarantined: unbacked number not in evidence allowlist")
                else:
                    notes.append(f"shadow: unbacked number(s) in internal_report {unbacked_ir}")
            if unbacked_cs or unbacked_ir:
                notes.append("needs_enrichment: unbacked numbers present")

        # R-CLAIM: named-event provenance — measure (shadow) or qualify (enforce) unbacked named specifics.
        # A cited/collected event is backed (entity in the allowlist); only invented specifics are flagged.
        if getattr(get_config(), "claim_provenance_enabled", True):
            cs_txt = (getattr(cs, "summary_text", "") if cs is not None else "") or ""
            ir_txt = (getattr(ir, "report_text", "") if ir is not None else "") or ""
            ub_cs = unbacked_entities(cs_txt, entity_allow, known_names)
            ub_ir = unbacked_entities(ir_txt, entity_allow, known_names)
            cl_total["client_summary"] += len(extract_claim_entities(cs_txt))
            cl_total["internal_report"] += len(extract_claim_entities(ir_txt))
            cl_unbacked["client_summary"] += len(ub_cs)
            cl_unbacked["internal_report"] += len(ub_ir)
            if ub_cs and getattr(get_config(), "claim_provenance_enforce", False) and cs is not None:
                new_cs, k = qualify_unbacked_claims(cs_txt, ub_cs)
                if k:
                    cs.summary_text = new_cs
                    notes.append(f"qualified: unbacked named claim(s) generalized in client_summary {ub_cs}")
            elif ub_cs:
                notes.append(f"shadow: unbacked named claim(s) in client_summary {ub_cs}")
            if ub_ir:
                notes.append(f"shadow: unbacked named claim(s) in internal_report {ub_ir}")

        status = "quarantined" if quarantine else "ok"
        _stamp(rec, status, notes)
        if ir is not None:
            _stamp(ir, status, notes)
        if cs is not None:
            _stamp(cs, status, notes)

    # TFS-10: one (client, cluster) per report — quarantine duplicates, keeping the first.
    seen: dict[tuple, int] = {}
    for i, rec in enumerate(recs):
        ck = (rec.client_name, rec.cluster_id)
        if ck in seen:
            dnote = f"quarantined: duplicate client-cluster {ck} (first at #{seen[ck]})"
            for obj in (rec,
                        reports[i] if i < len(reports) else None,
                        summaries[i] if i < len(summaries) else None):
                if obj is not None:
                    obj.validation_status = "quarantined"
                    obj.validation_notes = [*list(obj.validation_notes), dnote]
        else:
            seen[ck] = i

    quarantined = sum(1 for r in recs if getattr(r, "validation_status", "ok") != "ok")
    print(f"[validation_gate] {len(recs)} artifact(s) checked, {quarantined} quarantined")

    # R2-4: per-(node, model) hallucinated-number rate — both nodes are rendered by call_synthesis (gemini_model).
    model = getattr(get_config(), "gemini_model", "")
    np_metric: dict[str, dict] = {}
    for node in ("client_summary", "internal_report"):
        total = np_total[node]
        unb = np_unbacked[node]
        np_metric[node] = {
            "model": model,
            "total_numerals": total,
            "unbacked": unb,
            "hallucinated_number_rate": round(unb / total, 4) if total else 0.0,
        }
    print(
        f"[validation_gate] hallucinated_number_rate — "
        f"client_summary {np_unbacked['client_summary']}/{np_total['client_summary']} "
        f"({np_metric['client_summary']['hallucinated_number_rate']:.0%}), "
        f"internal_report {np_unbacked['internal_report']}/{np_total['internal_report']} "
        f"({np_metric['internal_report']['hallucinated_number_rate']:.0%}) · model={model}"
    )

    # R-CLAIM: per-node unbacked-named-claim rate (named events not traceable to collected evidence).
    cl_metric: dict[str, dict] = {}
    for node in ("client_summary", "internal_report"):
        total, unb = cl_total[node], cl_unbacked[node]
        cl_metric[node] = {"total_claims": total, "unbacked": unb,
                           "unbacked_claim_rate": round(unb / total, 4) if total else 0.0}
    print(
        f"[validation_gate] unbacked_claim_rate — "
        f"client_summary {cl_unbacked['client_summary']}/{cl_total['client_summary']} "
        f"({cl_metric['client_summary']['unbacked_claim_rate']:.0%}), "
        f"internal_report {cl_unbacked['internal_report']}/{cl_total['internal_report']} "
        f"({cl_metric['internal_report']['unbacked_claim_rate']:.0%})"
    )
    return {
        "recommendations": recs,
        "internal_reports": reports,
        "client_summaries": summaries,
        "numeric_provenance_metric": np_metric,
        "claim_provenance_metric": cl_metric,
    }
