import logging
from datetime import date, timedelta

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)


def get_client_revenue_handles(sb, client_id: str) -> dict:
    try:
        resp = (
            sb.table(m.USER_METRICS_TABLE)
            .select(",".join(m.USER_METRICS_JOIN_COLS))
            .eq("client_id", str(client_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else {}
    except Exception as e:
        log.error("[revenue_context] revenue handles for %s FAILED (DB may be unreachable): %s", client_id, e, exc_info=True)
        return {}


def get_gsc_demand(sb, gsc_site_url: str, weeks: int = 4) -> list[dict]:
    if not gsc_site_url:
        return []
    since = str(date.today() - timedelta(weeks=weeks))
    try:
        resp = (
            sb.table(m.GSC_QPM_TABLE)
            .select(",".join(m.GSC_QPM_READ_COLS))
            .eq("site_url", gsc_site_url)
            .gte("metric_date", since)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        log.error("[revenue_context] GSC demand for %s FAILED (DB may be unreachable): %s", gsc_site_url, e, exc_info=True)
        return []


def get_ga4_revenue(sb, ga4_property_id: str, weeks: int = 4) -> list[dict]:
    if not ga4_property_id:
        return []
    since = str(date.today() - timedelta(weeks=weeks))
    try:
        resp = (
            sb.table(m.GA4_METRICS_TABLE)
            .select(",".join(m.GA4_METRICS_READ_COLS))
            .eq("property_id", ga4_property_id)
            .gte("metric_date", since)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        log.error("[revenue_context] GA4 revenue for %s FAILED (DB may be unreachable): %s", ga4_property_id, e, exc_info=True)
        return []


def map_gsc_queries_to_clusters(sb, client_id: str, gsc_rows: list[dict],
                                company_domain: str = "") -> dict[str, dict]:
    if not gsc_rows:
        return {}
    try:
        from scout.db.cluster_registry import fetch_cluster_registry
        registry = fetch_cluster_registry(sb, client_id, company_domain)
    except Exception as e:
        log.error("[revenue_context] cluster registry for %s FAILED — demand mapping will be empty: %s", client_id, e, exc_info=True)
        return {}
    return _map_query_to_clusters(registry, gsc_rows)


def _tokenize(text: str) -> set[str]:
    return set((text or "").lower().split())


def _cluster_tokens(registry: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for cid, info in (registry or {}).items():
        tokens: set[str] = set()
        for q in (info.get("queries") or []):
            tokens |= _tokenize(q)
        for k in (info.get("keywords") or []):
            tokens |= _tokenize(k)
        out[cid] = tokens
    return out


def _map_query_to_clusters(registry: dict, gsc_rows: list[dict]) -> dict[str, dict]:
    if not registry or not gsc_rows:
        return {}
    cluster_tokens = _cluster_tokens(registry)

    buckets: dict[str, dict] = {}
    min_overlap = 1
    for row in gsc_rows:
        query = row.get("query") or ""
        q_tokens = _tokenize(query)
        if not q_tokens:
            continue
        best_cid = None
        best_score = 0
        for cid, ctokens in cluster_tokens.items():
            if not ctokens:
                continue
            overlap = len(q_tokens & ctokens)
            score = overlap / max(len(q_tokens), 1)
            if overlap >= min_overlap and score > best_score:
                best_score = score
                best_cid = cid
        target = best_cid or "__unmapped__"
        b = buckets.setdefault(target, {"impressions": 0, "clicks": 0, "query_count": 0, "matched_queries": []})
        b["impressions"] += int(row.get("impressions") or 0)
        b["clicks"] += int(row.get("clicks") or 0)
        b["query_count"] += 1
        if len(b["matched_queries"]) < 20:
            b["matched_queries"].append(query)
    return buckets


def normalize_url(url) -> str:
    """Normalize an absolute URL or a GA4 path-only landing_page to a comparable path key.
    Strips scheme/host/query/fragment, lowercases, collapses the trailing slash — this join is
    where Tier-2 attribution coverage is won or lost (PRD §5.2 R2)."""
    s = str(url or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        s = s.split("://", 1)[1]
    for sep in ("?", "#"):
        if sep in s:
            s = s.split(sep, 1)[0]
    if not s.startswith("/"):
        s = "/" + s.split("/", 1)[1] if "/" in s else "/"
    while len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


def cluster_for_query(registry: dict, query_text: str) -> str | None:
    """Best token-overlap cluster id for one query (>=1 shared token), or None.
    Same scoring rule as _map_query_to_clusters so Channel-C mapping matches Tier-1 demand mapping."""
    q_tokens = _tokenize(query_text)
    if not q_tokens or not registry:
        return None
    best_cid, best_score = None, 0.0
    for cid, ctokens in _cluster_tokens(registry).items():
        if not ctokens:
            continue
        overlap = len(q_tokens & ctokens)
        score = overlap / max(len(q_tokens), 1)
        if overlap >= 1 and score > best_score:
            best_score, best_cid = score, cid
    return best_cid
