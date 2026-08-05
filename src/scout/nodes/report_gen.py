# report_gen.py — LangGraph node: produce internal team reports + client-facing summaries via Gemini.
# Purpose: Turns each Recommendation into a 6-section InternalReport and a <500-word ClientSummary, with fallbacks.
# Scope: Two large system prompts (internal + news-agent tone), LLM invocation, length guards, deterministic GEO-context injection (readiness + revenue).
# Consumers: scout/graph.py chains this after recommendation_generation; slack_delivery consumes the reports.
import json
import re

from scout.config import get_config
from scout.llm import call_synthesis
from scout.models.recommendation import ClientSummary, InternalReport, Recommendation
from scout.state import ScoutState

INTERNAL_REPORT_PROMPT = """You are a competitive intelligence analyst for a service business GEO agency.
You write detailed action reports for an internal delivery team.

FORMAT WITH THESE EXACT SECTIONS:

1. SOV MOVEMENT SUMMARY
Client SOV this week vs 4-week avg. Competitor SOV this week vs 4-week avg. Direction.

2. DISPLACEMENT ANALYSIS
Which competitor(s) gained. Correlation data. Or new entrant profile.

3. INVESTIGATION FINDINGS
By source: Website changes (URLs, schema), Review changes (platform, delta), Web intelligence (press, LinkedIn), AI citation shift (language change).

4. ROOT CAUSE ASSESSMENT
1-3 probable causes with confidence and evidence.

5. RECOMMENDED ACTIONS
3-5 actions. For EACH action write all of:
- Title: imperative verb + content type (e.g. "Publish a specialized-service page", "Add FAQPage schema").
- Sub-steps: 2-4 concrete steps a delivery owner can execute.
- Owner: the responsible function — Web, Content, Marketing, Partners, or CX.
- Timeline: the week window, consistent with the report's stated timeline (e.g. T+0-1, T+2-4).
- KPIs: 2-3 specific, measurable success signals naming the exact page / schema @type / platform / query
  (e.g. "service page indexed within 72h", "3 schema @types validated green in Rich Results Test",
  "2 trade-press citations within 30 days"). Never a vague KPI like "improve visibility" or "increase engagement".

6. MEASUREMENT PLAN
First line: do NOT state a recovery percentage or any projected SOV number unless that number is given in the
input; if none is provided, write "Forecast withheld — cause unconfirmed."
Then provide:
- Pre-launch checks: 3-5 items to confirm live before the T+7 re-scan (e.g. page indexed, schema validated, file deployed).
- Re-scan queries with win criteria: each exact query to re-scan at T+7, and what a "win" looks like for the client on it.
- KPI scorecard: a table | Action | Lead KPI | Target by T+7 | Owner | with one row per action above.
- Deliverables at T+7: the concrete artifacts that must exist (published URL, validation screenshot, review-platform link).
- Escalation: what to do if fewer than the target KPIs are green at T+7.

Be specific. Name pages. Name schema types. No vague advice.

UNITS: every share-of-voice movement is in percentage points (pp), never percent (%) — write "+1.5pp", never "+1.5%".

NEVER output literal square brackets or placeholder tokens (e.g. [Current Date], [Client Name], [content type], [Year]). Use the report date and the real values supplied in the input; if a value is unknown, omit it or state it in plain words.

NUMBERS: use only figures present in the input evidence. Never invent review counts, citation percentages, impressions, or recovery figures. If a metric is unknown, write "not measured"."""

CLIENT_SUMMARY_PROMPT = """You are a competitive intelligence news agent delivering a weekly briefing to a service business founder.

Think of yourself as their personal market radar. Every Monday, you tell them what moved in their competitive landscape, why it matters, and what their team is already doing about it. This is not a report they requested. This is a proactive alert from a partner who is watching the market for them.

TONE: Direct. Confident. Like a trusted advisor who texts the founder on Monday morning with "here's what you need to know this week." Not formal. Not corporate. Not a marketing report.

RULES:
- Maximum 500 words.
- Name the competitor. Name the client. These are known entities.
- Lead with what changed, not with context-setting.
- State facts. If we don't know something, say "we don't know yet."
- End with what the team is doing and when the client will see movement.
- The timeline field is authoritative. Use it exactly.
- Never output literal square brackets or placeholder tokens (e.g. [Client Name], [Current Date]). Use the real names and values; if unknown, omit.
- Never invent numbers (review counts, percentages, recovery figures); use only figures provided. If unknown, say "we don't know yet."

BANNED TERMS: Bright Data, scraping, API, schema markup, JSON-LD, Apify, Perplexity, Claude, Gemini, Grok, pipeline, node, crawler, z-score, standard deviation, investigation, LLM, SERP, structured data, FAQ markup, citation language, positioning shift, SOV detection, rolling average, web intelligence, content diff, competitor snapshot, evidence block, confidence level, NLP, algorithm.

ALSO BANNED: "may indicate", "suggesting", "appears to", "likely", "probably", "emphasizes", "it seems", "we believe", "our analysis shows"

PLAIN ENGLISH SWAPS:
"SOV" -> "AI search visibility"
"schema" -> "website structure"
"third-party signals" -> "online reputation"
"AI citation" -> "how AI recommends businesses in this space"
"displacement" -> "lost ground"
"cluster" -> use the actual cluster label name instead

FORMAT:

**[Client name] - [Cluster label] Weekly Update**

**This week:**
1-2 sentences. What happened. Who moved. By how much (use percentage points if SOV is available, skip if unavailable). No preamble, no "this week we observed" - just the news.

**Why it matters:**
2-3 sentences. What the competitor did or what shifted. Translate the probable_cause into language a founder with zero marketing background understands. Then state the gap in one sentence: what the client is missing that the competitor now has.

**What we're on it:**
2-3 sentences. Frame as work already in motion, not recommendations waiting for approval. "We're publishing..." not "We recommend publishing..." List the most impactful 2-3 actions only, not all 5.

**Next check-in:**
One sentence. Use the timeline field. Add: "We'll update you next Monday with fresh numbers."

TOTAL: 500 words max. Every sentence earns its place. If it doesn't tell the founder something they'd act on or care about, cut it."""


def _attributed_assets_line(rec, client_id) -> tuple[str, dict]:
    cfg = get_config()
    if not (cfg.revenue_layer_enabled and cfg.asset_attribution_enabled
            and cfg.revenue_asset_surfacing_enabled):
        return "", {}
    if not client_id:
        return "", {}
    try:
        from scout.db import sed_mapping as m
        from scout.db.supabase_client import get_sed_client
        sb = get_sed_client()
        # Only categories with an observable page-level link may total. `unavailable` rows carry NULL
        # dollars by construction, and categories are never summed together.
        rows = (sb.table(m.SCOUT_ASSET_ATTRIBUTION_TABLE)
                .select("attributed_revenue_usd,revenue_category")
                .eq("client_id", str(client_id))
                .eq("cluster_id", rec.cluster_id)
                .in_("revenue_category", ["recorded", "influenced"])
                .execute().data or [])
        rows = [r for r in rows if r.get("attributed_revenue_usd") is not None]
        if not rows:
            return "", {}
        categories = {r.get("revenue_category") for r in rows}
        if len(categories) > 1:
            print("[report_gen] mixed revenue categories for cluster — not totalling")
            return "", {}
        category = categories.pop()
        total = sum(float(r["attributed_revenue_usd"]) for r in rows)
        line = (
            f"\n\nASSET ATTRIBUTION (internal only): ${total:,.0f} [{category}] attributed to this "
            f"cluster's assets across {len(rows)} measured window row(s) — correlational, "
            f"post-publish window, not proven causal. Do not quote to clients as earned revenue."
        )
        figures = {"asset_attribution": {"total_usd": total, "row_count": len(rows)}}
        return line, figures
    except Exception as e:
        print(f"[report_gen] asset-attribution line skipped: {e}")
        return "", {}


def report_generation(state: ScoutState) -> dict:
    """LangGraph node: iterate recommendations and emit an InternalReport + ClientSummary pair for each.
    Supabase is the system of record; the final state is reconstructable from its rows by run_id."""
    recommendations = state.get("recommendations", [])
    sync_date = state.get("sync_date", "")
    client_uuid_by_name = {c.get("client_name"): c.get("client_id")
                           for c in state.get("clients", []) or []}
    internal_reports: list[InternalReport] = []
    client_summaries: list[ClientSummary] = []

    client_sov_lookup: dict[tuple[str, str], float] = {}
    for sov_rec in state.get("sov_tracking_records", []):
        if (
            hasattr(sov_rec, "client_id")
            and hasattr(sov_rec, "cluster_id")
            and hasattr(sov_rec, "client_sov_this_week")
            and sov_rec.client_sov_this_week is not None
        ):
            client_sov_lookup.setdefault(
                (sov_rec.client_id, sov_rec.cluster_id), sov_rec.client_sov_this_week
            )

    for _i, rec in enumerate(recommendations):
        rec_dict = rec.model_dump() if hasattr(rec, "model_dump") else rec
        client_sov_now = client_sov_lookup.get((rec.client_id, rec.cluster_id))
        print(f"[report_gen] Generating internal report for {rec.competitor_name}")

        internal = _generate_internal_report(rec, rec_dict, sync_date)
        attr_line, attr_figures = _attributed_assets_line(rec, client_uuid_by_name.get(rec.client_name))
        if attr_line:
            internal.report_text += attr_line
            rec.revenue_context = {**(rec.revenue_context or {}), **attr_figures}
        internal_reports.append(internal)

        print(f"[report_gen] Generating client summary for {rec.competitor_name}")
        client = _generate_client_summary(rec, rec_dict, client_sov_now)
        client_summaries.append(client)

    return {
        "internal_reports": internal_reports,
        "client_summaries": client_summaries,
    }


def _generate_internal_report(rec: Recommendation, rec_dict: dict, sync_date: str = "") -> InternalReport:
    """Call Gemini with the INTERNAL_REPORT_PROMPT and a JSON-serialized Recommendation, returning an InternalReport.
    Falls back to a deterministic 6-section template when the call fails or the response is under 200 chars."""
    user_message = (
        f"Write a full internal competitive intelligence report for this recommended action.\n\n"
        f"Report date (use this exact value wherever a date is needed; never write '[Current Date]'): {sync_date or 'not provided'}\n\n"
        f"Recommendation data:\n{json.dumps(rec_dict, default=str)}\n\n"
        f"Use the 6 required sections. Be specific. Min 200 characters total."
        f"{_internal_geo_block(rec)}"
        f"{_internal_gap_block(rec)}"
    )
    try:
        report_text = call_synthesis(
            system_prompt=INTERNAL_REPORT_PROMPT,
            user_message=user_message,
            expect_json=False,
            node_name="report_generation_internal",
            trigger_key=f"{rec.competitor_name}::{rec.cluster_id}",
        )
        if isinstance(report_text, dict):
            report_text = json.dumps(report_text, indent=2)
        if len(report_text) < 200:
            report_text = _internal_fallback_text(rec)
    except Exception as e:
        print(f"[report_gen] internal report Claude call failed: {e}")
        report_text = _internal_fallback_text(rec)

    report = InternalReport(
        client_id=rec.client_id,
        client_name=rec.client_name,
        competitor_name=rec.competitor_name,
        cluster_id=rec.cluster_id,
        cluster_label=rec.cluster_label,
        priority=rec.priority,
        report_text=report_text,
    )
    print(report_text[:300])
    return report


def _generate_client_summary(rec: Recommendation, rec_dict: dict, client_sov_now: float | None = None) -> ClientSummary:
    """Call Gemini with the CLIENT_SUMMARY_PROMPT (news-agent tone) to produce a <=500-word, banned-terms-clean summary.
    Hard-caps text to 3500 chars and falls back to rec.summary when the call fails."""
    sov_line = f"{client_sov_now:.1f}pp" if client_sov_now is not None else "unavailable (baseline pending)"
    user_message = (
        f"Write this week's competitive intelligence briefing.\n\n"
        f"Client: {rec.client_name}\n"
        f"Competitor: {rec.competitor_name}\n"
        f"Cluster label: {rec.cluster_label}\n"
        f"Shift type: {rec.shift_type}\n"
        f"Priority: {rec.priority}\n"
        f"Client SOV this week: {sov_line}\n"
        f"Summary: {rec.summary}\n"
        f"Probable cause: {rec.probable_cause}\n"
        f"Gap analysis: {rec.gap_analysis}\n"
        f"Actions (pick 2-3 most impactful): {rec.action_bullets}\n"
        f"Timeline (use exactly): {rec.timeline}\n"
        f"{_client_geo_block(rec)}\n"
        f"{_client_gap_block(rec)}\n\n"
        f"Follow the news-agent format. Name the client and competitor. No banned terms. No hedge words."
    )
    try:
        def _call():
            text = call_synthesis(
                system_prompt=CLIENT_SUMMARY_PROMPT,
                user_message=user_message,
                expect_json=False,
                node_name="report_generation_client",
                trigger_key=f"{rec.competitor_name}::{rec.cluster_id}",
            )
            return json.dumps(text, indent=2, ensure_ascii=False) if isinstance(text, dict) else text

        summary_text = _call()

        # R1-3: length check (NOT an exception path) — a successful call can silently return ""
        # (llm.py: content or ""). Retry once, then fall back to a deterministic template, and only
        # leave "" (which validation_gate quarantines) when no structured fields exist.
        min_chars = get_config().client_summary_min_chars
        if len((summary_text or "").strip()) < min_chars:
            print(f"[report_gen] client summary empty/short (len={len((summary_text or '').strip())}) -> retry")
            summary_text = _call()
        if len((summary_text or "").strip()) < min_chars:
            template = _client_fallback_text(rec, client_sov_now)
            if template.strip():
                print("[report_gen] client summary still short after retry -> deterministic template")
                summary_text = template
            else:
                print("[report_gen] client summary unrecoverable (no structured fields) -> quarantine")
                summary_text = ""

        if len(summary_text) > 3500:
            summary_text = summary_text[:3497] + "..."
    except Exception as e:
        print(f"[report_gen] client summary Claude call failed: {e}")
        summary_text = rec.summary

    summary = ClientSummary(
        client_id=rec.client_id,
        client_name=rec.client_name,
        competitor_name=rec.competitor_name,
        cluster_id=rec.cluster_id,
        cluster_label=rec.cluster_label,
        summary_text=summary_text,
    )
    print(summary_text[:200])
    return summary


def _internal_fallback_text(rec: Recommendation) -> str:
    """Return a deterministic 6-section internal report string built entirely from the Recommendation fields.
    Used when Gemini is unavailable so downstream consumers still receive a schema-satisfying InternalReport."""
    return (
        f"1. SOV MOVEMENT SUMMARY\n"
        f"Competitor {rec.competitor_name} triggered a {rec.shift_type} alert on cluster {rec.cluster_label}. "
        f"Priority: {rec.priority}.\n\n"
        f"2. DISPLACEMENT ANALYSIS\n"
        f"{rec.probable_cause}\n\n"
        f"3. INVESTIGATION FINDINGS\n"
        f"Gap analysis: {rec.gap_analysis}\n\n"
        f"4. ROOT CAUSE ASSESSMENT\n"
        f"Probable cause (confidence: {rec.confidence}): {rec.probable_cause}\n\n"
        f"5. RECOMMENDED ACTIONS\n"
        + "\n".join(f"- {b}" for b in rec.action_bullets)
        + f"\n\n6. MEASUREMENT PLAN\nRe-scan cluster queries at T+7. Monitor SOV recovery vs {rec.competitor_name} baseline."
    )


# R1-3: client-prompt plain-English swaps (report_gen CLIENT_SUMMARY_PROMPT lines 63-69), applied to
# structured fields so the deterministic fallback never emits the prompt's BANNED TERMS.
_PLAIN_SWAPS = [
    (r"\bshare of voice\b", "AI search visibility"),
    (r"\bSOV\b", "AI search visibility"),
    (r"\bschema markup\b", "website structure"),
    (r"\bstructured data\b", "website structure"),
    (r"\bschema\b", "website structure"),
    (r"\bthird[- ]party signals\b", "online reputation"),
    (r"\bAI citations?\b", "how AI recommends businesses in this space"),
    (r"\bdisplacement\b", "lost ground"),
]


def _plainify(text: str) -> str:
    """Apply the client-prompt plain-English swaps (SOV->visibility, schema->website structure, ...) to a structured string.
    Deterministic + case-insensitive; keeps the deterministic client template free of the prompt's banned technical terms."""
    out = text or ""
    for pat, repl in _PLAIN_SWAPS:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


def _client_fallback_text(rec: Recommendation, client_sov_now: float | None = None) -> str:
    """Build a deterministic news-agent client summary from Recommendation fields when the LLM returns empty/short.
    Mirrors _internal_fallback_text's role; applies plain-English swaps, emits no BANNED TERMS, returns '' when no usable fields exist."""
    if not (rec.probable_cause or rec.gap_analysis or rec.action_bullets):
        return ""
    this_week = f"{rec.competitor_name} moved on {rec.cluster_label} this week."
    if client_sov_now is not None:
        this_week += f" Your AI search visibility here is {client_sov_now:.1f} points."
    why = _plainify(rec.probable_cause or "").strip()
    gap = _plainify(rec.gap_analysis or "").strip()
    why_matters = " ".join(p for p in (why, gap) if p) or "We're still confirming exactly what shifted."
    actions = [b for b in (rec.action_bullets or []) if b][:2]
    if actions:
        on_it = "We're already on it: " + "; ".join(_plainify(a) for a in actions) + "."
    else:
        on_it = "Our team is already working the response."
    next_in = (rec.timeline or "We'll have an early read shortly.").strip()
    next_in = f"{next_in} We'll update you next Monday with fresh numbers."
    return (
        f"**{rec.client_name} - {rec.cluster_label} Weekly Update**\n\n"
        f"**This week:**\n{this_week}\n\n"
        f"**Why it matters:**\n{why_matters}\n\n"
        f"**What we're on it:**\n{on_it}\n\n"
        f"**Next check-in:**\n{next_in}"
    )


def _internal_geo_block(rec: Recommendation) -> str:
    """Build a deterministic GEO-context block (client readiness + revenue) appended to the internal-report prompt.
    Returns '' when no readiness/revenue is carried, so an empty section is never emitted."""
    lines = []
    cr = rec.client_readiness or {}
    if cr and cr.get("confidence") not in (None, "none"):
        present = ", ".join(cr.get("schema_types_present") or []) or "none"
        missing = ", ".join(cr.get("schema_types_missing") or []) or "none"
        lines += [
            "CLIENT SITE READINESS (the client's own site — fold into INVESTIGATION FINDINGS / ROOT CAUSE / RECOMMENDED ACTIONS / MEASUREMENT PLAN):",
            f"  AI-access files: llms.txt={cr.get('llms_txt_present')} ai_bots={cr.get('ai_bots_present')} robots.txt={cr.get('robots_present')}",
            f"  Schema @types present: {present}",
            f"  Schema @types missing (recommend adding): {missing}",
        ]
        comp = rec.competitor_ai_access or {}
        if comp:
            lines.append(
                f"  AI-access vs competitor: competitor llms.txt={comp.get('llms_txt')} "
                f"ai_bots={comp.get('ai_bots')} robots.txt={comp.get('robots_txt')}"
            )
    rev = rec.revenue_context or {}
    if rev and rev.get("average_order_value"):
        lines += [
            "REVENUE CONTEXT (for framing impact magnitude only):",
            f"  avg_order_value: {rev.get('average_order_value')} {rev.get('currency', '')}".rstrip(),
            f"  conversion_rate: {rev.get('conversion_rate')}  estimated_ctr: {rev.get('estimated_ctr')}",
        ]
    if not lines:
        return ""
    return "\n\nGEO CONTEXT (use specifically — name the files and @types):\n" + "\n".join(lines)


def _client_geo_block(rec: Recommendation) -> str:
    """Build a plain-English, banned-terms-safe client-site readiness note for the client-summary prompt.
    Returns '' when nothing to add; never emits 'schema'/'structured data'/@type names (the prompt bans them)."""
    cr = rec.client_readiness or {}
    if not cr or cr.get("confidence") in (None, "none"):
        return ""
    notes = []
    if not cr.get("llms_txt_present"):
        notes.append("the site is missing an AI-access file that tells AI assistants how to read it")
    if not cr.get("robots_present"):
        notes.append("the site is missing basic access rules for automated visitors")
    if cr.get("schema_types_missing"):
        notes.append("the site's pages are missing details that help AI describe the business accurately")
    comp = rec.competitor_ai_access or {}
    if comp.get("llms_txt") and not cr.get("llms_txt_present"):
        notes.append("a competitor already publishes that AI-access file while this site does not")
    if not notes:
        return ""
    return "Client-site readiness (translate to plain English, no technical words): " + "; ".join(notes) + "."


# Plain-English, forward-looking phrasing per gap AREA — keeps the client summary banned-terms-safe (no schema/@type/robots).
_GAP_AREA_PLAIN = {
    "reviews": "publish and grow third-party reviews so AI assistants trust and recommend the business",
    "entity_authority": "claim the business's profiles on the major directories AI checks to confirm it is a real, established company",
    "ai_access": "add the access files that let AI assistants read the site",
    "structured_data": "add the behind-the-scenes page details that help AI describe the business accurately",
    "content_demand": "publish fuller pages about each offering so AI has real substance to cite",
    "competitive": "match the review and profile presence that higher-ranked competitors already have",
}


def _internal_gap_block(rec: Recommendation) -> str:
    """Deterministic client-side GEO gap actions (recon+readiness), priority-ranked, for the internal report prompt.
    Returns '' when no gaps are carried so an empty section is never emitted."""
    gaps = getattr(rec, "client_gaps", None) or []
    if not gaps:
        return ""
    lines = ["CLIENT GEO READINESS ACTIONS (the client's OWN-site gaps, priority-ranked — fold into RECOMMENDED ACTIONS):"]
    for g in gaps[:6]:
        lines.append(f"  [{g.get('priority')}] {g.get('title')} — {g.get('client_action')}")
    return "\n\nGEO READINESS ACTIONS:\n" + "\n".join(lines)


def _client_gap_block(rec: Recommendation) -> str:
    """Plain-English, forward-looking growth opportunities (one per gap area) for the client summary prompt.
    Never says the site is lacking (no-baseline-disclosure); banned-terms-safe. Returns '' when nothing to add."""
    gaps = getattr(rec, "client_gaps", None) or []
    seen: list[str] = []
    for g in gaps:
        phrase = _GAP_AREA_PLAIN.get(g.get("area"))
        if phrase and phrase not in seen:
            seen.append(phrase)
    if not seen:
        return ""
    return ("Growth opportunities to raise AI visibility (translate to plain English, forward-looking, never say the "
            "site is missing or lacking anything): " + "; ".join(seen[:4]) + ".")


