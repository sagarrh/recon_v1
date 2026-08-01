# field_resolution.py — LangGraph node: collapse per-competitor triggers into one verdict per client-cluster.
# Purpose: Group investigation_triggers by (client_id, cluster_id), pick the primary competitor, fold the rest into a field.
# Scope: Pure Python aggregation; leaves investigation_triggers intact so per-competitor investigation/audit is preserved. No LLM, no I/O.
# Consumers: scout/graph.py runs it after merge_triggers; recommendation_gen iterates the resulting cluster_verdicts.
from collections import defaultdict

from scout.models.sov import ClusterVerdict
from scout.nodes.triage_digest import build_triage_digest
from scout.state import ScoutState


def _delta_pp(trigger) -> float:
    """Return the competitor's signed share delta in pp: positive for gains/new entrants/displacements, negative for losses.
    Ranks competitors so the primary is the one that moved most in the client's disfavour."""
    mag = trigger.shift_magnitude or 0.0
    return -mag if trigger.shift_type == "loss" else mag


def field_resolution(state: ScoutState) -> dict:
    """LangGraph node: build one ClusterVerdict per (client_id, cluster_id) from the per-competitor triggers.
    Returns {'cluster_verdicts': [...]}; the per-competitor investigation_triggers list is left untouched."""
    triggers = state.get("investigation_triggers", []) or []
    groups: dict[tuple, list] = defaultdict(list)
    for t in triggers:
        groups[(t.client_id, t.cluster_id)].append(t)

    verdicts: list[ClusterVerdict] = []
    for (client_id, cluster_id), group in groups.items():
        primary = max(group, key=_delta_pp)
        field = [
            {
                "competitor": t.competitor_name,
                "competitor_domain": t.competitor_domain,
                "delta_pp": round(_delta_pp(t), 4),
            }
            for t in sorted(group, key=_delta_pp, reverse=True)
        ]
        verdicts.append(ClusterVerdict(
            client_id=client_id,
            client_name=primary.client_name,
            cluster_id=cluster_id,
            cluster_label=primary.cluster_label,
            primary_competitor=primary.competitor_name,
            primary_competitor_domain=primary.competitor_domain,
            field=field,
            shift_type=primary.shift_type,
            delta_client=primary.client_sov_change,
            correlated_displacement=any(t.correlated_displacement for t in group),
            triage_severity=getattr(primary, "triage_severity", "") or "",
            investigation_priority=primary.investigation_priority,
            noise=(getattr(primary, "triage_severity", "") == "NOISE"),
            graduation_regime=getattr(primary, "graduation_regime", "news_mode"),
        ))

    digest = build_triage_digest(verdicts)
    actionable = sum(1 for v in verdicts if not v.noise)
    print(f"[field_resolution] {len(triggers)} trigger(s) -> {len(verdicts)} verdict(s), {actionable} actionable")
    return {"cluster_verdicts": verdicts, "triage_digest": digest}
