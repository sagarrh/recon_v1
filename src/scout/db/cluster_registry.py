# cluster_registry.py — Build a cluster registry from GEO queries_groups_data / keywords_groups_data.
# Purpose: Source (cluster_id, label, queries, keywords) so clusters exist even when a client has no SOV/AI/report data.
# Scope: Read-only parse of two GEO jsonb columns scoped to one client; in-memory only. Lowest-confidence rung.
# Consumers: scout/db/reader.py Rung 4 (behind geo_cluster_registry_enabled) for otherwise-empty clients.
import json
import logging

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)


def fetch_cluster_registry(sb, client_id: str, company_domain: str = "") -> dict[str, dict]:
    """Return {cluster_id: {"cluster_label", "queries", "keywords"}} for a client from the GEO groups jsonb.
    cluster_id is the GEO group key ('0','1',...) stringified; label is the truncated first query/keyword."""
    queries_by_group = _walk_groups(_fetch_blob(sb, m.QUERIES_GROUPS_TABLE, m.QUERIES_GROUPS_COL, client_id), "Queries")
    keywords_by_group = _walk_groups(_fetch_blob(sb, m.KEYWORDS_GROUPS_TABLE, m.KEYWORDS_GROUPS_COL, client_id), "Keywords")
    out: dict[str, dict] = {}
    for gid in set(queries_by_group) | set(keywords_by_group):
        if not gid:
            continue
        queries = queries_by_group.get(gid, [])
        keywords = keywords_by_group.get(gid, [])
        seed = (queries[0] if queries else (keywords[0] if keywords else gid))
        out[gid] = {
            "cluster_label": str(seed)[:60],
            "queries": queries,
            "keywords": keywords,
        }
    return out


def _fetch_blob(sb, table: str, column: str, client_id: str):
    """Fetch and parse the single jsonb groups column for a client from a GEO groups table, or None on miss.
    Reads the most recent row; returns the decoded object (dict) or None when absent/unparseable."""
    try:
        resp = (
            sb.table(table)
            .select(f"{column},created_at")
            .eq("client_id", str(client_id))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        log.warning("[cluster_registry] fetch %s for %s failed: %s", table, client_id, e)
        return None
    rows = resp.data or []
    if not rows:
        return None
    value = rows[0].get(column)
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _walk_groups(blob, leaf_key: str) -> dict[str, list[str]]:
    """Walk {domain:{region:{"0":{leaf_key:[...]}}}} and return {group_key: [items]} flattening domain + region.
    leaf_key is 'Queries' or 'Keywords'; returns {} on any shape mismatch so a malformed blob is harmless."""
    out: dict[str, list[str]] = {}
    if not isinstance(blob, dict):
        return out
    for _domain, regions in blob.items():
        if not isinstance(regions, dict):
            continue
        for _region, groups in regions.items():
            if not isinstance(groups, dict):
                continue
            for gkey, payload in groups.items():
                if not isinstance(payload, dict):
                    continue
                items = payload.get(leaf_key)
                if isinstance(items, list):
                    out.setdefault(str(gkey), [])
                    out[str(gkey)].extend([str(x) for x in items if x])
    return out
