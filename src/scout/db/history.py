import logging
from datetime import date, timedelta

from scout.db import sed_mapping as m
from scout.models.history import ClusterHistory, CompetitorHistory

log = logging.getLogger(__name__)


def _cutoff(weeks: int) -> str:
    return str(date.today() - timedelta(weeks=weeks))


def _safe(label: str, fn) -> list[dict]:
    try:
        return fn().data or []
    except Exception as e:
        log.warning("[history] %s failed: %s", label, e)
        return []


def sov_tracking_timeline(sb, client_id, cluster_id, competitor_name, weeks) -> list[dict]:
    return _safe(
        "sov_tracking_timeline",
        lambda: sb.table(m.SCOUT_SOV_TRACKING_TABLE)
        .select("week_date,current_sov,rolling_mean,rolling_std,z_score,sov_delta_pp,alert_flag")
        .eq("client_id", client_id)
        .eq("cluster_id", cluster_id)
        .eq("competitor_name", competitor_name)
        .gte("week_date", _cutoff(weeks))
        .order("week_date", desc=False)
        .execute(),
    )


def investigation_timeline(sb, client_id, cluster_id, competitor_name, weeks) -> list[dict]:
    return _safe(
        "investigation_timeline",
        lambda: sb.table(m.SCOUT_INVESTIGATIONS_TABLE)
        .select("week_date,website_changes,third_party_signals,ai_citation_changes")
        .eq("client_id", client_id)
        .eq("cluster_id", cluster_id)
        .eq("competitor_name", competitor_name)
        .gte("week_date", _cutoff(weeks))
        .order("week_date", desc=False)
        .execute(),
    )


def recommendation_timeline(sb, client_id, cluster_id, weeks) -> list[dict]:
    return _safe(
        "recommendation_timeline",
        lambda: sb.table(m.SCOUT_RECOMMENDATIONS_TABLE)
        .select("week_date,competitor_name,rec_type,confidence,probable_cause,gap_analysis")
        .eq("client_id", client_id)
        .eq("cluster_id", cluster_id)
        .gte("week_date", _cutoff(weeks))
        .order("week_date", desc=False)
        .execute(),
    )


def outcome_timeline(sb, client_id, cluster_id, weeks) -> list[dict]:
    return _safe(
        "outcome_timeline",
        lambda: sb.table(m.SCOUT_OUTCOMES_TABLE)
        .select("week_date,baseline_client_sov_pp,baseline_primary_competitor,sov_delta_pp,recovered,executed")
        .eq("client_id", client_id)
        .eq("cluster_id", cluster_id)
        .gte("week_date", _cutoff(weeks))
        .order("week_date", desc=False)
        .execute(),
    )


def ai_citation_timeline(sb, cluster_id, weeks) -> list[dict]:
    # ai_responses retains only ~AI_LOOKBACK_WEEKS weeks; return whatever exists, never error on thin data.
    return _safe(
        "ai_citation_timeline",
        lambda: sb.table(m.AI_MONITORING_TABLE)
        .select("week_date,platform,citations_data")
        .eq("cluster_id", cluster_id)
        .gte("week_date", _cutoff(weeks))
        .order("week_date", desc=False)
        .execute(),
    )


def load_cluster_history(sb, verdict, weeks) -> ClusterHistory:
    names: list[str] = []
    if verdict.primary_competitor:
        names.append(verdict.primary_competitor)
    for f in (verdict.field or []):
        n = f.get("competitor")
        if n and n not in names:
            names.append(n)
    competitors = [
        CompetitorHistory(
            competitor_name=n,
            sov_track=sov_tracking_timeline(sb, verdict.client_id, verdict.cluster_id, n, weeks),
            investigations=investigation_timeline(sb, verdict.client_id, verdict.cluster_id, n, weeks),
        )
        for n in names
    ]
    return ClusterHistory(
        client_id=verdict.client_id,
        cluster_id=verdict.cluster_id,
        cluster_label=verdict.cluster_label,
        competitors=competitors,
        prior_recommendations=recommendation_timeline(sb, verdict.client_id, verdict.cluster_id, weeks),
        outcomes=outcome_timeline(sb, verdict.client_id, verdict.cluster_id, weeks),
        ai_citation_weeks=ai_citation_timeline(sb, verdict.cluster_id, weeks),
    )
