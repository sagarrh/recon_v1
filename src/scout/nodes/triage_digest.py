# triage_digest.py — Build the one-line-per-cluster triage digest from resolved verdicts.
# Purpose: A scannable "N client-clusters · K actionable · rest monitored" view so NOISE collapses to one line each.
# Scope: Pure function over a ClusterVerdict list; no LLM, no I/O. Attached to state by field_resolution, rendered by slack_delivery.
# Consumers: scout/nodes/field_resolution.py (producer), scout/nodes/slack_delivery.py (renderer).

_RANK = {"CRITICAL": 0, "ELEVATED": 1, "WIN": 2, "WATCH": 3, "NOISE": 4, "": 5}


def build_triage_digest(verdicts) -> list[dict]:
    """Return one digest row per verdict, sorted by severity then competitive movement within tier.

    Ordering is deliberately not revenue-driven: a dollar figure derived from SOV ranked clusters by how
    small their SOV was, not by how commercially important they were. Until the commercial priority
    score lands, severity plus the size of the primary competitor's move is the honest ordering."""
    rows = []
    for v in verdicts:
        primary_delta = next(
            (f.get("delta_pp") for f in (v.field or []) if f.get("competitor") == v.primary_competitor),
            None,
        )
        rows.append({
            "client_name": v.client_name,
            "cluster_label": v.cluster_label,
            "primary_competitor": v.primary_competitor,
            "primary_delta_pp": primary_delta,
            "delta_client_pp": v.delta_client,
            "severity": v.triage_severity or "NOISE",
            "noise": bool(v.noise),
            "revenue_category": getattr(v, "revenue_category", "unavailable"),
        })
    rows.sort(key=lambda r: (_RANK.get(r["severity"], 5),
                             -abs(r["primary_delta_pp"] or 0.0)))
    return rows
