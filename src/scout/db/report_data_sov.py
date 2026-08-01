# report_data_sov.py — Derive per-week SOV scores from report_data.ai_visibility_data (durable GEO snapshot).
# Purpose: Third fallback rung so clients with no sov_weekly AND no usable ai_responses still produce triggers.
# Scope: Read-only compute over Supabase report_data rows; in-memory only — never writes to any table.
# Consumers: scout/db/reader.py wires this in after compute_sov_from_ai; reuses reader._to_week_date for bucketing.
import json
import logging
from collections import defaultdict
from datetime import date, timedelta

from scout.db import sed_mapping as m
from scout.utils import (
    normalize_domain as _normalise_domain,
)
from scout.utils import (
    parse_json_cell as _parse_jsonish,
)
from scout.utils import (
    to_week_date as _to_week_date,
)

log = logging.getLogger(__name__)


def compute_sov_from_report_data(
    sb,
    client_id: str,
    competitor_names: list[str],
    competitor_domains: dict[str, str],
    lookback_weeks: int,
    client_name: str = "",
) -> dict[str, dict]:
    """Parse report_data.ai_visibility_data for one client into {cluster_id: {"label", "entries":[{entity_name, week, sov_score}]}}.
    Buckets each report by created_at -> Monday week; sov_score is the within-(cluster, week) mention share as percentage-points (0-100, ×100) to match sov_weekly + the pp severity floors."""
    cutoff_iso = (date.today() - timedelta(weeks=lookback_weeks)).isoformat()
    try:
        resp = (
            sb.table(m.REPORT_DATA_TABLE)
            .select(
                f"{m.REPORT_DATA_COLS['ai_visibility_data']},{m.REPORT_DATA_COLS['created_at']}"
            )
            .eq(m.REPORT_DATA_COLS["client_id"], str(client_id))   # client_id is TEXT in report_data
            .gte(m.REPORT_DATA_COLS["created_at"], cutoff_iso)
            .order(m.REPORT_DATA_COLS["created_at"], desc=False)
            .limit(200)
            .execute()
        )
    except Exception as e:
        log.warning("[report_data_sov] fetch for client=%s failed: %s", client_id, e)
        return {}
    rows = resp.data or []
    if not rows:
        return {}

    name_lower_to_canonical = {n.lower(): n for n in competitor_names if n}
    domain_to_name: dict[str, str] = {}
    for name, dom in competitor_domains.items():
        nd = _normalise_domain(dom)
        if nd:
            domain_to_name[nd] = name
    client_lower = client_name.lower() if client_name else ""

    bucket: dict[tuple, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    labels: dict[str, str] = {}
    for row in rows:
        week = _to_week_date(row.get(m.REPORT_DATA_COLS["created_at"]))
        avd = _parse_jsonish(row.get(m.REPORT_DATA_COLS["ai_visibility_data"]))
        if not isinstance(avd, dict):
            continue
        for cluster_id, blob in avd.items():
            if not cluster_id or not isinstance(blob, dict):
                continue
            label = blob.get("cluster_name") or blob.get("cluster_label")
            if label and str(cluster_id) not in labels:
                labels[str(cluster_id)] = str(label)
            results = blob.get("ai_overview_results")
            if not isinstance(results, list):
                continue
            ec = bucket[(str(cluster_id), week)]
            for res in results:
                if isinstance(res, dict):
                    _count_companies(
                        res.get("companies_data"), name_lower_to_canonical,
                        domain_to_name, client_lower, ec,
                    )

    out: dict[str, dict] = {}
    for (cluster_id, week), ec in bucket.items():
        total = sum(ec.values())
        if total <= 0:
            continue
        node = out.setdefault(cluster_id, {"label": labels.get(cluster_id, cluster_id), "entries": []})
        for entity, cnt in ec.items():
            node["entries"].append({"entity_name": entity, "week": week, "sov_score": 100.0 * cnt / total})

    log.info("[report_data_sov] client=%s -> %d cluster(s) from %d report(s)", client_id, len(out), len(rows))
    return out


def get_geo_ai_baseline_bundle(sb, client_id: str, cluster_id: str) -> dict:
    """Build {current_responses, baseline_responses} for a cluster from report_data.ai_visibility_data.
    Latest report carrying the cluster = current; the next-older report with it = baseline; entries are {platform, query, response}."""
    try:
        resp = (
            sb.table(m.REPORT_DATA_TABLE)
            .select(f"{m.REPORT_DATA_COLS['ai_visibility_data']},{m.REPORT_DATA_COLS['created_at']}")
            .eq(m.REPORT_DATA_COLS["client_id"], str(client_id))   # TEXT client_id
            .order(m.REPORT_DATA_COLS["created_at"], desc=True)
            .limit(5)
            .execute()
        )
    except Exception as e:
        log.warning("[report_data_sov] ai baseline fetch for client=%s failed: %s", client_id, e)
        return {}
    rows = resp.data or []
    sides: list[list[dict]] = []
    for row in rows:
        results = _cluster_results(row.get(m.REPORT_DATA_COLS["ai_visibility_data"]), cluster_id)
        if results:
            sides.append(results)
        if len(sides) == 2:
            break
    if not sides:
        return {}
    return {"current_responses": sides[0], "baseline_responses": sides[1] if len(sides) > 1 else []}


def _cluster_results(ai_visibility_data, cluster_id: str) -> list[dict]:
    """Extract one report's ai_overview_results for a cluster into [{platform, query, response}] entries.
    Returns [] when the blob lacks the cluster or is malformed; serializes non-string answers to JSON text."""
    avd = _parse_jsonish(ai_visibility_data)
    if not isinstance(avd, dict):
        return []
    blob = avd.get(str(cluster_id))
    if not isinstance(blob, dict):
        return []
    results = blob.get("ai_overview_results")
    out: list[dict] = []
    if isinstance(results, list):
        for res in results:
            if not isinstance(res, dict):
                continue
            ans = res.get("answers")
            if ans is None:
                ans = res.get("answer")
            if isinstance(ans, (list, dict)):
                ans = json.dumps(ans, ensure_ascii=False)
            out.append({
                "platform": res.get("platform") or "ai_overview",
                "query": res.get("query") or "",
                "response": ans or "",
            })
    return out


def _count_companies(companies_data, name_lower_to_canonical, domain_to_name, client_lower, entity_counts) -> None:
    """Tally competitor + client mentions inside one ai_overview_result.companies_data blob into entity_counts.
    Tolerant of GEO's shapes (dict-of-{metric} / dict-of-number / list-of-name|dict); unmatched entities are skipped."""
    cd = _parse_jsonish(companies_data)
    if isinstance(cd, dict):
        for key, val in cd.items():
            entity = _match_entity(str(key), name_lower_to_canonical, domain_to_name, client_lower)
            if entity:
                entity_counts[entity] += _weight_of(val)
    elif isinstance(cd, list):
        for item in cd:
            key = item if isinstance(item, str) else None
            if isinstance(item, dict):
                key = (item.get("name") or item.get("company") or item.get("company_name")
                       or item.get("domain") or item.get("url"))
            if not key:
                continue
            entity = _match_entity(str(key), name_lower_to_canonical, domain_to_name, client_lower)
            if entity:
                entity_counts[entity] += _weight_of(item) if isinstance(item, dict) else 1.0


def _match_entity(key: str, name_lower_to_canonical, domain_to_name, client_lower) -> str | None:
    """Resolve a companies_data key (a name or a domain) to a canonical competitor name or the __client__ sentinel.
    Matches exact name, then domain, then bidirectional substring (tolerates legal suffixes like 'P.C.'); else None."""
    low = key.strip().lower()
    if not low:
        return None
    if low in name_lower_to_canonical:
        return name_lower_to_canonical[low]
    nd = _normalise_domain(key)
    if nd and nd in domain_to_name:
        return domain_to_name[nd]
    # Fuzzy: report entity-name and configured competitor differ by a suffix/abbreviation
    # (e.g. "Fish & Richardson" vs "Fish & Richardson P.C."). Length guard avoids spurious hits;
    # longest-wins keeps overlapping configured names deterministic and order-independent.
    best, best_len = None, -1
    for cname_low, canonical in name_lower_to_canonical.items():
        if len(cname_low) >= 4 and (cname_low in low or low in cname_low) and len(cname_low) > best_len:
            best, best_len = canonical, len(cname_low)
    if best is not None:
        return best
    if client_lower and (low == client_lower or client_lower in low or (len(low) >= 4 and low in client_lower)):
        return "__client__"
    return None


def _weight_of(val) -> float:
    """Return a numeric weight for a companies_data value: an explicit metric/score when present, else 1.0 (presence).
    Reads total_visibility/visibility/count/mentions/score from dicts; coerces bare numbers; defaults to presence=1.0."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        for field in ("total_visibility", "visibility", "count", "mentions", "score"):
            if field in val:
                try:
                    return float(val[field])
                except (TypeError, ValueError):
                    return 1.0
        return 1.0
    return 1.0

