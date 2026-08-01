# sov_detection.py — LangGraph node: rolling-avg / z-score based SOV shift detection and trigger generation.
# Purpose: Evaluates each (client, cluster, competitor) tuple, classifies shifts (gain/loss/displacement/new_entrant), emits triggers.
# Scope: Pure-Python math (no LLM calls): rolling stats, z-score, displacement correlation, volume cap, news_mode fast path.
# Consumers: scout/graph.py entry node; its SOVTrackingRecord and InvestigationTrigger lists feed every downstream node.
import logging
import math
from datetime import date

from scout.config import get_config
from scout.models.sov import CycleSummary, InvestigationTrigger, SOVTrackingRecord
from scout.state import ScoutState

log = logging.getLogger(__name__)

_SEVERITY_URGENT = ("CRITICAL", "ELEVATED")
# R4-4: a z-score is never trusted on thinner history than this — below it, classification uses absolute-pp severity only.
Z_SCORE_MIN_HISTORY_WEEKS = 4


def _severity_from_pp(shift_type, shift_magnitude, client_delta, config) -> str:
    """Classify a competitor×cluster move into an absolute-pp severity tier (no z-score; news_mode-safe).
    Loss/gain asymmetry: client losses route to ELEVATED/CRITICAL; a competitor gain from a ~zero client base caps at WATCH."""
    floor = config.min_floor_pp
    cd = client_delta if client_delta is not None else 0.0
    comp_gain = (shift_magnitude or 0.0) if shift_type in ("gain", "new_entrant", "displacement") else 0.0
    if cd >= config.win_pp:
        return "WIN"
    if cd <= -config.crit_loss_pp:
        return "CRITICAL"
    if cd <= -floor:
        return "ELEVATED"
    if comp_gain >= floor:
        return "WATCH"
    return "NOISE"


def _priority_from_severity(severity, base_priority) -> str:
    """Upgrade investigation_priority to 'urgent' for CRITICAL/ELEVATED; otherwise keep the rule-based priority.
    Keeps the existing urgent|standard|opportunistic enum intact while letting magnitude raise urgency."""
    if severity in _SEVERITY_URGENT:
        return "urgent"
    return base_priority


def compute_rolling_stats(scores: list[float]) -> tuple[float, float]:
    """Return (mean, population_stddev) for a list of SOV scores; (0.0, 0.0) for an empty list.
    Uses population std-dev (divide by N, not N-1) so short windows don't blow up the z-score denominator."""
    if not scores:
        return 0.0, 0.0
    avg = sum(scores) / len(scores)
    variance = sum((s - avg) ** 2 for s in scores) / len(scores)
    return avg, math.sqrt(variance)


def compute_z_score(
    current: float, avg: float, std_dev: float, flat_threshold: float
) -> tuple[float | None, bool]:
    """Return (z_score, usable) where z is None when std_dev=0; usable True iff |current-avg| exceeds flat_threshold.
    The flat-baseline branch lets detection still fire on first-material-move after multiple identical weeks."""
    if std_dev == 0:
        return (None, abs(current - avg) > flat_threshold)
    return ((current - avg) / std_dev, True)


def classify_alert(
    competitor_z: float | None,
    client_z: float | None,
    history_weeks: int,
    sov_this_week: float,
    flat_exceeded: bool,
    config,
    flat_delta: float | None = None,
) -> tuple[str | None, str | None, bool]:
    """Apply the rule ladder (new_entrant → displacement → gain → loss) and return (alert_type, priority, correlated).
    Displacement requires both competitor_z above +threshold and client_z below -threshold on the same cluster-week."""
    # R4-4: below the explicit min-history floor, classify on absolute movement only — never trust a z-score.
    if history_weeks < Z_SCORE_MIN_HISTORY_WEEKS and sov_this_week > 0:
        return ("new_entrant", "standard", False)
    flat_gain = flat_exceeded and (flat_delta is None or flat_delta > 0)
    flat_loss = flat_exceeded and flat_delta is not None and flat_delta < 0
    comp_above = (
        competitor_z is not None and competitor_z > config.displacement_threshold_sd
    ) or (competitor_z is None and flat_gain)
    client_below = client_z is not None and client_z < -config.displacement_threshold_sd
    if comp_above and client_below:
        return ("displacement", "urgent", True)
    comp_gain = (
        competitor_z is not None and competitor_z > config.detection_threshold_sd
    ) or (competitor_z is None and flat_gain)
    if comp_gain:
        priority = "urgent" if (competitor_z and competitor_z > 3.0) else "standard"
        return ("gain", priority, False)
    if (competitor_z is not None and competitor_z < -config.detection_threshold_sd) or (
        competitor_z is None and flat_loss
    ):
        return ("loss", "opportunistic", False)
    return (None, None, False)


def sov_detection(state: ScoutState) -> dict:
    """LangGraph entry node: scan every client-cluster-competitor tuple, emit SOVTrackingRecords + InvestigationTriggers + CycleSummary.
    news_mode short-circuits the stats path and emits a routine 'gain' investigation per competitor for day-1 validation."""
    config = get_config()
    sync_date = date.fromisoformat(state["sync_date"])
    clients = state["clients"]

    tracking_records: list[SOVTrackingRecord] = []
    triggers: list[InvestigationTrigger] = []
    clusters_processed = 0
    pairs_evaluated = 0
    data_quality_errors = 0
    no_significant_shifts_clients: list[str] = []
    capped_investigations: list[str] = []
    by_type: dict[str, int] = {"gain": 0, "loss": 0, "displacement": 0, "new_entrant": 0}

    for client in clients:
        client_id = client["client_id"]
        client_name = client["client_name"]
        client_triggers: list[InvestigationTrigger] = []

        for cluster in client["clusters"]:
            cluster_id = cluster["cluster_id"]
            cluster_label = cluster["cluster_label"]
            clusters_processed += 1

            client_history_scores = [
                e["sov_score"] for e in cluster.get("client_sov_history", [])
            ][-4:]
            client_avg, client_std = compute_rolling_stats(client_history_scores)
            client_current = cluster.get("client_sov_this_week", 0.0)
            client_z, _ = compute_z_score(
                client_current, client_avg, client_std, config.flat_baseline_threshold_pp
            )
            client_sov_change = client_current - client_avg if client_avg else None

            for competitor in cluster.get("competitors", []):
                pairs_evaluated += 1
                comp_name = competitor["competitor_name"]
                comp_domain = competitor["competitor_domain"]
                sov_this_week = competitor["sov_this_week"]

                if sov_this_week < 0 or sov_this_week > 100:
                    data_quality_errors += 1
                    tracking_records.append(
                        SOVTrackingRecord(
                            client_id=client_id,
                            cluster_id=cluster_id,
                            competitor_name=comp_name,
                            week_date=sync_date,
                            sov_score=sov_this_week,
                            alert_triggered=False,
                            alert_reason="data_quality_error: sov_score out of 0-100 range",
                        )
                    )
                    continue

                # R4-1: per-cluster graduation — a competitor with enough CONSECUTIVE SOV history graduates out of
                # the news_mode validation posture into the real z-score / absolute-floor path below. Stateless,
                # zero new queries (the history is already loaded). consecutive_weeks comes from the reader (R4-2);
                # len(sov_history) is the pre-R4-2 fallback.
                consecutive_weeks = competitor.get("consecutive_weeks", len(competitor.get("sov_history", [])))
                # R4-4: re-entry hysteresis — a clean contiguous run >= threshold graduates; if a short gap broke
                # the run but the cluster had enough total recent history (was graduated), keep it graduated until
                # the null streak reaches regraduation_null_weeks. A single null week never de-graduates.
                graduated_clean = consecutive_weeks >= config.graduation_threshold_weeks
                total_recent_weeks = len(competitor.get("sov_history", [])) + (1 if sov_this_week else 0)
                null_streak = max(0, config.graduation_threshold_weeks - consecutive_weeks)
                was_graduated = total_recent_weeks >= config.graduation_threshold_weeks
                graduated = graduated_clean or (was_graduated and null_streak < config.regraduation_null_weeks)

                if config.news_mode and not graduated:
                    record, trigger = _build_news_trigger(
                        client_id=client_id,
                        client_name=client_name,
                        cluster_id=cluster_id,
                        cluster_label=cluster_label,
                        comp_name=comp_name,
                        comp_domain=comp_domain,
                        sov_this_week=sov_this_week,
                        client_current=client_current,
                        client_sov_change=client_sov_change,
                        sync_date=sync_date,
                    )
                    _sev = _severity_from_pp("gain", sov_this_week, client_sov_change, config)
                    trigger.triage_severity = _sev
                    trigger.investigation_priority = _priority_from_severity(_sev, trigger.investigation_priority)
                    trigger.graduation_regime = "news_mode"   # R4-3
                    tracking_records.append(record)
                    client_triggers.append(trigger)
                    by_type["gain"] = by_type.get("gain", 0) + 1
                    continue

                history_entries = competitor.get("sov_history", [])
                history_scores = [e["sov_score"] for e in history_entries][-4:]
                history_weeks = len(history_scores)

                rolling_avg, rolling_std = compute_rolling_stats(history_scores)
                change_vs_avg = sov_this_week - rolling_avg if history_scores else None

                z_score, flat_exceeded = compute_z_score(
                    sov_this_week, rolling_avg, rolling_std, config.flat_baseline_threshold_pp
                )

                alert_type, priority, correlated = classify_alert(
                    z_score,
                    client_z,
                    history_weeks,
                    sov_this_week,
                    flat_exceeded,
                    config,
                    flat_delta=change_vs_avg,
                )

                shift_magnitude = abs(change_vs_avg) if change_vs_avg is not None else sov_this_week

                alert_reason = None
                if alert_type == "new_entrant":
                    alert_reason = (
                        f"{comp_name} not previously cited on this cluster — "
                        f"appeared with SOV {sov_this_week}pp this week. First observation."
                    )
                elif alert_type == "displacement":
                    comp_sd = f" ({z_score:.2f} SD)" if z_score is not None else ""
                    client_sd = f" ({client_z:.2f} SD)" if client_z is not None else ""
                    alert_reason = (
                        f"{comp_name} gained {change_vs_avg:.2f}pp SOV{comp_sd} "
                        f"while client lost {abs(client_sov_change):.2f}pp{client_sd} — "
                        f"correlated displacement detected."
                    )
                elif alert_type == "gain":
                    base = (
                        f"{comp_name} SOV increased {change_vs_avg:.2f}pp vs "
                        f"4-week avg of {rolling_avg:.2f}pp"
                    )
                    alert_reason = base + (
                        f" — {z_score:.2f} SD above rolling baseline." if z_score is not None else "."
                    )
                elif alert_type == "loss":
                    base = (
                        f"{comp_name} SOV dropped {abs(change_vs_avg):.2f}pp vs "
                        f"4-week avg of {rolling_avg:.2f}pp"
                    )
                    alert_reason = base + (
                        f" — {z_score:.2f} SD below rolling baseline. Opportunity to investigate."
                        if z_score is not None else ". Opportunity to investigate."
                    )

                record = SOVTrackingRecord(
                    client_id=client_id,
                    cluster_id=cluster_id,
                    competitor_name=comp_name,
                    week_date=sync_date,
                    sov_score=sov_this_week,
                    rolling_avg_4w=rolling_avg if history_scores else None,
                    rolling_std_4w=rolling_std if history_scores else None,
                    change_vs_avg=change_vs_avg,
                    z_score=z_score,
                    client_sov_this_week=client_current,
                    client_sov_change_vs_avg=client_sov_change,
                    alert_triggered=alert_type is not None,
                    alert_type=alert_type,
                    alert_reason=alert_reason,
                )
                tracking_records.append(record)

                if alert_type is not None:
                    trig_reason = alert_reason or ""
                    trigger = InvestigationTrigger(
                        client_id=client_id,
                        client_name=client_name,
                        competitor_name=comp_name,
                        competitor_domain=comp_domain,
                        cluster_id=cluster_id,
                        cluster_label=cluster_label,
                        shift_type=alert_type,
                        shift_magnitude=shift_magnitude,
                        shift_in_sd_units=z_score,
                        client_sov_change=client_sov_change,
                        correlated_displacement=correlated,
                        investigation_priority=priority,
                        triage_reason=trig_reason,
                    )
                    _sev = _severity_from_pp(alert_type, shift_magnitude, client_sov_change, config)
                    trigger.triage_severity = _sev
                    trigger.investigation_priority = _priority_from_severity(_sev, trigger.investigation_priority)
                    trigger.graduation_regime = "graduated" if graduated else "news_mode"   # R4-3
                    client_triggers.append(trigger)
                    by_type[alert_type] = by_type.get(alert_type, 0) + 1

        if not client_triggers:
            no_significant_shifts_clients.append(client_name)
        kept, dropped = _apply_volume_cap(client_triggers, config.max_investigations_per_client)
        capped_investigations.extend(dropped)
        triggers.extend(kept)

    cycle_summary = CycleSummary(
        run_id=state["run_id"],
        sync_date=sync_date,
        clients_processed=len(clients),
        clusters_processed=clusters_processed,
        competitor_cluster_pairs_evaluated=pairs_evaluated,
        investigations_triggered=len(triggers),
        investigations_by_type=by_type,
        no_significant_shifts_clients=no_significant_shifts_clients,
        capped_investigations=capped_investigations,
        data_quality_errors=data_quality_errors,
    )

    print(f"\n[sov_detection] {len(triggers)} trigger(s) detected:")
    for t in triggers:
        print(f"  > {t.competitor_name} | {t.cluster_label} | {t.shift_type} | {t.investigation_priority}")

    return {
        "sov_tracking_records": tracking_records,
        "investigation_triggers": triggers,
        "cycle_summary": cycle_summary,
    }


def _build_news_trigger(
    client_id: str,
    client_name: str,
    cluster_id: str,
    cluster_label: str,
    comp_name: str,
    comp_domain: str,
    sov_this_week: float,
    client_current: float,
    client_sov_change: float | None,
    sync_date: date,
) -> tuple[SOVTrackingRecord, InvestigationTrigger]:
    """Build a deterministic (SOVTrackingRecord, InvestigationTrigger) pair for news_mode — no stats, every comp fires.
    Used for day-1 client validation before enough history exists to compute meaningful rolling baselines."""
    record = SOVTrackingRecord(
        client_id=client_id,
        cluster_id=cluster_id,
        competitor_name=comp_name,
        week_date=sync_date,
        sov_score=sov_this_week,
        client_sov_this_week=client_current,
        client_sov_change_vs_avg=client_sov_change,
        alert_triggered=True,
        alert_type="gain",
        alert_reason="news_mode: routine investigation",
    )
    trigger = InvestigationTrigger(
        client_id=client_id,
        client_name=client_name,
        competitor_name=comp_name,
        competitor_domain=comp_domain,
        cluster_id=cluster_id,
        cluster_label=cluster_label,
        shift_type="gain",
        shift_magnitude=sov_this_week,
        shift_in_sd_units=None,
        client_sov_change=client_sov_change,
        correlated_displacement=False,
        investigation_priority="standard",
        triage_reason=f"News-mode investigation for {comp_name} on {cluster_label}",
    )
    return record, trigger


def _apply_volume_cap(
    triggers: list[InvestigationTrigger], max_per_client: int
) -> tuple[list[InvestigationTrigger], list[str]]:
    """Enforce max_investigations_per_client by keeping all urgent first, then top-sd-unit non-urgents up to the cap.
    Returns (kept_triggers, dropped_keys) where dropped keys are 'competitor::cluster' strings; max_per_client<=0 means unlimited (R0-6)."""
    if max_per_client <= 0 or len(triggers) <= max_per_client:
        return triggers, []
    urgent = [t for t in triggers if t.investigation_priority == "urgent"]
    remaining = [t for t in triggers if t.investigation_priority != "urgent"]
    remaining_sorted = sorted(
        remaining,
        key=lambda t: (t.shift_in_sd_units or 0),
        reverse=True,
    )
    combined = urgent + remaining_sorted
    kept = combined[:max_per_client]
    dropped = [f"{t.competitor_name}::{t.cluster_id}" for t in combined[max_per_client:]]
    if dropped:
        log.warning("[sov_detection] dropped %d capped investigation(s): %s", len(dropped), dropped)
    return kept, dropped
