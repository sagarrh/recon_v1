import logging

from scout.config import get_config
from scout.db.history import load_cluster_history
from scout.keys import make_cluster_key
from scout.models.history import ClusterHistory
from scout.state import ScoutState

log = logging.getLogger(__name__)


def historical_context(state: ScoutState) -> dict:
    if not get_config().deep_recommendation_enabled:
        return {}
    from scout.db.supabase_client import get_sed_client
    sb = get_sed_client()
    weeks = get_config().deep_recommendation_lookback_weeks
    out: dict[str, ClusterHistory] = {}
    for verdict in state.get("cluster_verdicts", []) or []:
        try:
            out[make_cluster_key(verdict.client_id, verdict.cluster_id)] = load_cluster_history(sb, verdict, weeks)
        except Exception as e:
            log.warning("[historical_context] %s/%s failed: %s", verdict.client_id, verdict.cluster_id, e)
    print(f"[historical_context] loaded history for {len(out)} verdict(s)")
    return {"historical_context": out}
