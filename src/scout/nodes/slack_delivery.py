# slack_delivery.py — LangGraph node: push recommendations + internal reports + cycle issues to Slack.
# Purpose: Formats block_kit messages and dispatches to the intel (recs) and alerts (reports + cycle issues) webhooks.
# Scope: Emoji-by-priority, fallback text, chunking long reports, cycle-issue block assembly, no state mutation.
# Consumers: scout/graph.py terminal node; skips entirely when slack_enabled is false.
from scout.config import get_config
from scout.integrations.slack import chunk_text, post_alert, post_intel, truncate_block
from scout.state import ScoutState

_PRIORITY_EMOJI = {
    "urgent": ":rotating_light:",
    "standard": ":mag:",
    "opportunistic": ":bulb:",
}


def slack_delivery(state: ScoutState) -> dict:
    """LangGraph node: fan every recommendation, internal report, and cycle-issue digest to Slack.
    No-op and returns {} when slack_enabled is false; otherwise logs intel/alert counts on success."""
    config = get_config()
    if not config.slack_enabled:
        return {}

    recommendations = state.get("recommendations", [])
    internal_reports = state.get("internal_reports", [])
    client_summaries = state.get("client_summaries", [])
    cycle_summary = state.get("cycle_summary")
    run_id = state.get("run_id", "")
    sync_date = state.get("sync_date", "")

    intel_sent = 0
    alerts_sent = 0

    digest = state.get("triage_digest", []) or []
    if digest and post_intel(
        "Weekly triage digest", _build_triage_digest_block(digest, run_id, sync_date)
    ):
        intel_sent += 1

    summary_by_artifact = {}
    for cs in client_summaries:
        summary_by_artifact[(cs.client_id, cs.cluster_id)] = cs.summary_text

    for rec in recommendations:
        if getattr(rec, "validation_status", "ok") != "ok":
            continue
        summary_text = summary_by_artifact.get((rec.client_id, rec.cluster_id), "")
        blocks = _build_recommendation_blocks(rec, summary_text, run_id, sync_date)
        fallback = f"{rec.competitor_name} — {rec.shift_type} on {rec.cluster_label}"
        if post_intel(fallback, blocks):
            intel_sent += 1

    for rep in internal_reports:
        if getattr(rep, "validation_status", "ok") != "ok":
            continue
        chunks = chunk_text(rep.report_text)
        for i, chunk in enumerate(chunks):
            header = f"*Internal Report — {rep.competitor_name} on {rep.cluster_label}*"
            if len(chunks) > 1:
                header += f"  _(part {i+1}/{len(chunks)})_"
            if i == 0:
                header += f"\n_Priority: {rep.priority.upper()} · Client: {rep.client_name} · Run {run_id[:8]} · {sync_date}_"
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": header}},
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": truncate_block(chunk)}},
            ]
            fallback = f"Internal Report — {rep.competitor_name} on {rep.cluster_label}"
            if post_alert(fallback, blocks):
                alerts_sent += 1

    # R1-4: surface quarantined artifacts (skipped above) to the alerts channel with reason + record link,
    # so a quarantine is no longer a silent dead end.
    quarantine_block = _build_quarantine_block(recommendations, internal_reports, run_id, sync_date)
    if quarantine_block:
        fallback = f"Scout quarantined artifacts — run {run_id[:8]}"
        if post_alert(fallback, quarantine_block):
            alerts_sent += 1

    quarantine_rate = _quarantine_rate(recommendations)
    issues_block = _build_cycle_issues_block(
        cycle_summary, run_id, sync_date,
        quarantine_rate=quarantine_rate,
        threshold=config.quarantine_rate_alert_threshold,
    )
    if issues_block:
        fallback = f"Scout cycle issues — run {run_id[:8]}"
        if post_alert(fallback, issues_block):
            alerts_sent += 1

    print(f"[slack_delivery] intel messages: {intel_sent}, alerts: {alerts_sent}")
    return {}


def _quarantine_rate(recommendations) -> float:
    """Return quarantined-recommendation count / total recommendations (0.0 when there are none).
    Drives the threshold line folded into the cycle-issues alert (R1-4)."""
    recs = recommendations or []
    total = len(recs)
    if total == 0:
        return 0.0
    q = sum(1 for r in recs if getattr(r, "validation_status", "ok") != "ok")
    return q / total


def _build_quarantine_block(recs, reports, run_id: str, sync_date: str) -> list | None:
    """Compose a block_kit alert listing each quarantined rec/report with its reason(s) + a record link, or None when none.
    Record link = run_id + competitor::cluster_id — the in-memory artifact has no Supabase UUID; the natural key locates the row."""
    lines = []
    for rec in recs or []:
        if getattr(rec, "validation_status", "ok") == "ok":
            continue
        notes = "; ".join(getattr(rec, "validation_notes", []) or []) or "quarantined"
        lines.append(
            f"• [{getattr(rec, 'cluster_label', '')}] {getattr(rec, 'competitor_name', '')} — {notes} "
            f"· run {run_id[:8]} · {getattr(rec, 'cluster_id', '')}"
        )
    for rep in reports or []:
        if getattr(rep, "validation_status", "ok") == "ok":
            continue
        notes = "; ".join(getattr(rep, "validation_notes", []) or []) or "quarantined"
        lines.append(
            f"• [report: {getattr(rep, 'cluster_label', '')}] {getattr(rep, 'competitor_name', '')} — {notes} "
            f"· run {run_id[:8]}"
        )
    if not lines:
        return None
    header = f":no_entry: *Scout quarantined artifacts* — run `{run_id[:8]}` · {sync_date} · {len(lines)} item(s)"
    body = "\n".join(lines)
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": truncate_block(body)}},
    ]


def _build_recommendation_blocks(rec, summary_text: str, run_id: str, sync_date: str) -> list:
    """Compose the block_kit list for a single Recommendation (header, cause, actions, optional summary, footer).
    Caps per-section text via truncate_block so each block stays within Slack's ~3000-char ceiling."""
    emoji = _PRIORITY_EMOJI.get(rec.priority, ":mag:")
    header = (
        f"{emoji} *[{rec.priority.upper()}]* {rec.competitor_name} — "
        f"{rec.shift_type} on {rec.cluster_label}\n"
        f"_{rec.client_name}_"
    )
    cause = f"*Probable cause:* {truncate_block(rec.probable_cause, 1200)}"
    actions_text = "\n".join(f"• {b}" for b in rec.action_bullets[:5])
    actions = f"*Recommended actions:*\n{truncate_block(actions_text, 1500)}"
    footer = f"_Run {run_id[:8]} · {sync_date} · type: {rec.type} · confidence: {rec.confidence}_"

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": cause}},
        {"type": "section", "text": {"type": "mrkdwn", "text": actions}},
    ]
    if summary_text:
        summary_block = f"*Client summary:*\n{truncate_block(summary_text, 2000)}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary_block}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return blocks


def _build_triage_digest_block(digest: list, run_id: str, sync_date: str) -> list:
    """Compose a block_kit summary: counts header + one line per client-cluster, most urgent first.
    NOISE clusters render as 'monitored, no action' so the watch is visibly live without a full report."""
    actionable = sum(1 for r in digest if not r.get("noise"))
    monitored = len(digest) - actionable
    header = (
        f"*Weekly triage* — {len(digest)} client-cluster(s) · {actionable} actionable · "
        f"{monitored} monitored\n_Run {run_id[:8]} · {sync_date}_"
    )
    lines = []
    for r in digest:
        delta = r.get("primary_delta_pp")
        delta_s = f"{delta:+.2f}pp" if isinstance(delta, (int, float)) else "n/a"
        flag = "monitored, no action" if r.get("noise") else "actionable"
        lines.append(
            f"• [{r.get('severity', 'NOISE')}] {r.get('client_name', '')} / "
            f"{r.get('cluster_label', '')} — {r.get('primary_competitor', '')} {delta_s} ({flag})"
        )
    body = "\n".join(lines) if lines else "_no clusters_"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": truncate_block(body)}},
    ]


def _build_cycle_issues_block(cycle_summary, run_id: str, sync_date: str,
                              quarantine_rate: float | None = None, threshold: float | None = None) -> list | None:
    """Return a single block_kit section summarizing data_quality_errors + capped_investigations + over-threshold quarantine rate, or None when clean.
    Avoids sending a no-issues alert every cycle; preview caps the capped list at 10 entries with a '+N more' tag (cycle_summary may be None)."""
    dqe = getattr(cycle_summary, "data_quality_errors", 0) or 0
    capped = getattr(cycle_summary, "capped_investigations", []) or []
    over_quarantine = (quarantine_rate is not None and threshold is not None and quarantine_rate > threshold)
    if dqe == 0 and not capped and not over_quarantine:
        return None

    lines = [f":warning: *Scout cycle issues* — run `{run_id[:8]}` · {sync_date}"]
    if dqe:
        lines.append(f"• Data quality errors: *{dqe}*")
    if capped:
        capped_preview = ", ".join(capped[:10])
        more = f" (+{len(capped)-10} more)" if len(capped) > 10 else ""
        lines.append(f"• Capped investigations ({len(capped)}): {capped_preview}{more}")
    if over_quarantine:
        lines.append(f"• Quarantine rate *{quarantine_rate:.0%}* exceeds threshold *{threshold:.0%}*")

    return [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]
