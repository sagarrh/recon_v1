# reader.py — Data loader for the Scout pipeline (Supabase-backed).
# Purpose: Builds the initial weekly SOV bundle by reading directly from Supabase (clients + sov_weekly), no SQLite hop.
# Scope: Entry function load_data() + Supabase helper; does not mutate state, only builds the initial weekly SOV bundle.
# Consumers: run.py calls load_data(client_filter) once at startup; no other consumer.
import logging
from collections import defaultdict
from datetime import date, timedelta

from scout.config import get_config
from scout.db import sed_mapping as m
from scout.db.sov_compute import compute_sov_from_ai
from scout.utils import parse_json_cell as _parse_json_col
from scout.utils import to_week_date as _to_week_date

log = logging.getLogger(__name__)


def load_data(client_filter: str | None = None, *, client_id: str | None = None) -> dict:
    """Return the weekly SOV bundle, optionally filtered to a single client by name.
    Pulls directly from Supabase (clients + sov_weekly); SOV is computed from ai_responses where sov_weekly is absent."""
    return _load_from_supabase_direct(client_filter, client_id=client_id)


def _resolve(d: dict, candidates: list[str], default=None):
    """Return the first candidate key present in d, else default; tolerates upstream Supabase column renames in JSON cells.
    Used so a mixed competitors[] array (strings vs dicts vs aliased keys) still resolves to a name+domain pair."""
    for key in candidates:
        if key in d:
            return d[key]
    return default


def _normalise_competitors(competitors_raw, competitors_url_raw) -> list[dict]:
    """Convert the clients.competitors JSONB column into [{competitor_name, competitor_domain, aliases}] in the bundle shape.
    Handles three formats: list of strings, list of dicts with name/domain, or a parallel competitors_url list of strings/dicts."""
    out: list[dict] = []
    if not isinstance(competitors_raw, list):
        return out
    url_list = competitors_url_raw if isinstance(competitors_url_raw, list) else []
    for i, c in enumerate(competitors_raw):
        name = ""
        domain = ""
        if isinstance(c, str):
            name = c
            if i < len(url_list):
                entry = url_list[i]
                if isinstance(entry, str):
                    domain = entry
                elif isinstance(entry, dict):
                    domain = _resolve(entry, ["domain", "url", "website"], "") or ""
        elif isinstance(c, dict):
            name = _resolve(c, ["name", "company_name", "company", "competitor_name"], "") or ""
            domain = _resolve(c, ["domain", "url", "website"], "") or ""
        if name:
            out.append({
                "competitor_name": name,
                "competitor_domain": domain,
                "aliases": [name],
            })
    return out


def _consecutive_week_count(entries: list[dict]) -> int:
    """Count the run of consecutive Monday-spaced weeks ending at the most recent entry (entries are week-descending).
    Stops at the first >7-day gap so a non-contiguous history doesn't graduate a cluster (R4-2 feeds R4-1)."""
    if not entries:
        return 0
    def _parse(w):
        try:
            return date.fromisoformat(str(w)[:10])
        except Exception:
            return None
    prev = _parse(entries[0].get("week"))
    if prev is None:
        return 0
    count = 1
    for e in entries[1:]:
        cur = _parse(e.get("week"))
        if cur is None or (prev - cur).days != 7:
            break
        count += 1
        prev = cur
    return count


def _fetch_ai_clusters_for_client(sb, client_id: str) -> list[tuple]:
    """Return distinct (cluster_id, cluster_name) pairs that ai_responses has for a client within the AI lookback window.
    Used to discover clusters for the compute fallback when sov_weekly has no rows for the client."""
    cutoff = str(date.today() - timedelta(weeks=m.AI_LOOKBACK_WEEKS))
    try:
        resp = (
            sb.table(m.AI_MONITORING_TABLE)
            .select(f"{m.AI_MONITORING_COLS['cluster_id']},{m.AI_MONITORING_COLS['cluster_name']}")
            .eq(m.AI_MONITORING_COLS["client_id"], client_id)
            .gte("week_date", cutoff)
            .limit(2000)
            .execute()
        )
    except Exception as e:
        log.warning("[reader] _fetch_ai_clusters_for_client(%s) failed: %s", client_id, e)
        return []
    seen: dict[str, str] = {}
    for row in resp.data or []:
        cid = row.get(m.AI_MONITORING_COLS["cluster_id"]) or ""
        cname = row.get(m.AI_MONITORING_COLS["cluster_name"]) or ""
        if cid and cid not in seen:
            seen[cid] = cname or cid
    return sorted(seen.items())


def _index_top_companies(top, week: str) -> list[tuple]:
    """Return [(entity_name, {"week", "sov_score"})] from a sov_weekly top_companies payload on one 0-100 scale.
    Parses the canonical LIST shape [{name, sov_score}] (scout-sync.transformSovWeekly output, already x100) and the legacy DICT shape {wk:{entity:{total_visibility}}} (0-1, scaled x100 to match)."""
    out: list[tuple] = []
    if isinstance(top, list):
        for entry in top:
            if not isinstance(entry, dict):
                continue
            entity_name = entry.get("name")
            if not entity_name:
                continue
            try:
                score = float(entry.get("sov_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            out.append((entity_name, {"week": week, "sov_score": score}))
    elif isinstance(top, dict):
        for _week_key, companies in top.items():
            if not isinstance(companies, dict):
                continue
            for entity_name, metrics in companies.items():
                if not entity_name or not isinstance(metrics, dict):
                    continue
                try:
                    score = float(metrics.get(m.TOP_COMPANIES_SCORE_FIELD, 0.0) or 0.0) * 100.0
                except (TypeError, ValueError):
                    score = 0.0
                out.append((entity_name, {"week": week, "sov_score": score}))
    return out


def _match_competitor(entity_name: str, name_lower_to_canonical: dict) -> str | None:
    """Resolve a sov_weekly entity name to a configured competitor: exact (case-insensitive) then the MOST SPECIFIC (longest) bidirectional substring match (>=4 chars).
    Longest-wins keeps overlapping names ('Apple' vs 'Apple Music') deterministic and order-independent; bridges legal-suffix drift ('RSM' vs 'RSM US LLP'); mirrors report_data_sov._match_entity's name path."""
    low = (entity_name or "").strip().lower()
    if not low:
        return None
    if low in name_lower_to_canonical:
        return name_lower_to_canonical[low]
    best, best_len = None, -1
    for cname_low, canonical in name_lower_to_canonical.items():
        if len(cname_low) >= 4 and (cname_low in low or low in cname_low) and len(cname_low) > best_len:
            best, best_len = canonical, len(cname_low)
    return best


def _load_from_supabase_direct(
    client_filter: str | None, *, client_id: str | None = None
) -> dict:
    """Pull clients + sov_weekly directly from Supabase and build the weekly SOV bundle in memory — no SQLite hop.
    Returns the {'sync_date', 'clients': [...]} shape sov_detection consumes."""
    from scout.db.supabase_client import get_sed_client
    sb = get_sed_client()

    clients_resp = (
        sb.table(m.CLIENT_TABLE)
        .select(
            f"{m.CLIENT_COLS['client_id']},"
            f"{m.CLIENT_COLS['client_name']},"
            f"{m.CLIENT_COLS['company_domain']},"
            f"{m.CLIENT_COLS['company_website']},"
            f"{m.CLIENT_COLS['competitors']},"
            f"{m.CLIENT_COLS['competitors_url']}"
        )
        .execute()
    )
    raw_clients = clients_resp.data or []
    if client_id:
        raw_clients = [
            row
            for row in raw_clients
            if str(row.get(m.CLIENT_COLS["client_id"], "")) == str(client_id)
        ]
    if client_filter:
        raw_clients = [r for r in raw_clients if r.get(m.CLIENT_COLS["client_name"]) == client_filter]

    clients_cfg: list[dict] = []
    for row in raw_clients:
        client_id = row.get(m.CLIENT_COLS["client_id"], "")
        client_name = row.get(m.CLIENT_COLS["client_name"], "")
        if not client_id or not client_name:
            continue
        competitors = _normalise_competitors(
            _parse_json_col(row.get(m.CLIENT_COLS["competitors"])),
            _parse_json_col(row.get(m.CLIENT_COLS["competitors_url"])),
        )
        clients_cfg.append({
            "client_id": client_id,
            "client_name": client_name,
            "client_aliases": [client_name],
            "company_domain": row.get(m.CLIENT_COLS["company_domain"], "") or "",
            "company_website": row.get(m.CLIENT_COLS["company_website"], "") or "",
            "competitors": competitors,
        })

    if not clients_cfg:
        log.warning("[reader] no clients returned from Supabase (filter=%s)", client_filter)
        return {"sync_date": str(date.today()), "clients": []}

    cutoff = str(date.today() - timedelta(weeks=m.SOV_LOOKBACK_WEEKS))
    sov_resp = (
        sb.table(m.SOV_TABLE)
        .select(
            f"{m.SOV_COLS['client_id']},"
            f"{m.SOV_COLS['cluster_id']},"
            f"{m.SOV_COLS['cluster_name']},"
            f"{m.SOV_COLS['top_companies']},"
            f"{m.SOV_COLS['week_date']},"
            f"{m.SOV_COLS['created_at']}"
        )
        .gte(m.SOV_COLS["week_date"], cutoff)
        .order(m.SOV_COLS["week_date"], desc=False)
        .execute()
    )
    sov_rows = sov_resp.data or []

    cluster_labels: dict[tuple, str] = {}
    sov_index: dict[tuple, list[dict]] = defaultdict(list)
    client_names_by_id = {
        client["client_id"]: client["client_name"].strip().casefold()
        for client in clients_cfg
    }
    for row in sov_rows:
        client_id = row.get(m.SOV_COLS["client_id"], "")
        cluster_id = row.get(m.SOV_COLS["cluster_id"], "")
        cluster_name = row.get(m.SOV_COLS["cluster_name"], "") or cluster_id
        if cluster_id and cluster_name and client_id:
            cluster_labels[(client_id, cluster_id)] = cluster_name
        week = str(row.get(m.SOV_COLS["week_date"]) or _to_week_date(row.get(m.SOV_COLS["created_at"])))
        top = _parse_json_col(row.get(m.SOV_COLS["top_companies"]))
        for entity_name, entry in _index_top_companies(top, week):
            entity_key = (
                "__client__"
                if entity_name.strip().casefold() == client_names_by_id.get(client_id)
                else entity_name
            )
            sov_index[(client_id, cluster_id, entity_key)].append(entry)

    covered_pairs = {(k[0], k[1]) for k in sov_index}
    for client in clients_cfg:
        cid = client["client_id"]
        ai_clusters = _fetch_ai_clusters_for_client(sb, cid)
        if not ai_clusters:
            continue
        competitor_names = [c["competitor_name"] for c in client["competitors"]]
        competitor_domains = {c["competitor_name"]: c["competitor_domain"] for c in client["competitors"]}
        for cluster_id, cluster_name in ai_clusters:
            if not cluster_id or (cid, cluster_id) in covered_pairs:
                continue
            computed = compute_sov_from_ai(
                sb, cid, cluster_id, competitor_names, competitor_domains,
                m.SOV_LOOKBACK_WEEKS, client_name=client["client_name"],
            )
            if not computed:
                continue
            if cluster_name and (cid, cluster_id) not in cluster_labels:
                cluster_labels[(cid, cluster_id)] = cluster_name
            for entry in computed:
                sov_index[(cid, cluster_id, entry["entity_name"])].append({
                    "week": entry["week"],
                    "sov_score": entry["sov_score"],
                })
            covered_pairs.add((cid, cluster_id))

    # Rung 3: report_data.ai_visibility_data (durable GEO snapshot) for still-uncovered pairs.
    if get_config().geo_report_data_sov_enabled:
        from scout.db.report_data_sov import compute_sov_from_report_data
        for client in clients_cfg:
            cid = client["client_id"]
            competitor_names = [c["competitor_name"] for c in client["competitors"]]
            competitor_domains = {c["competitor_name"]: c["competitor_domain"] for c in client["competitors"]}
            per_cluster = compute_sov_from_report_data(
                sb, cid, competitor_names, competitor_domains,
                m.REPORT_DATA_LOOKBACK_WEEKS, client_name=client["client_name"],
            )
            for cluster_id, payload in per_cluster.items():
                if not cluster_id or (cid, cluster_id) in covered_pairs:
                    continue
                if payload.get("label") and (cid, cluster_id) not in cluster_labels:
                    cluster_labels[(cid, cluster_id)] = payload["label"]
                for entry in payload.get("entries", []):
                    sov_index[(cid, cluster_id, entry["entity_name"])].append({
                        "week": entry["week"],
                        "sov_score": entry["sov_score"],
                    })
                covered_pairs.add((cid, cluster_id))

    for key in sov_index:
        sov_index[key].sort(key=lambda e: e["week"], reverse=True)

    # Rung 4: query-cluster registry (flag OFF) — label-only clusters for clients with NO SOV data at all.
    # These carry no competitor scores (sov_this_week=0.0); under news_mode they still fire a trigger.
    extra_clusters: dict[str, set] = {}
    if get_config().geo_cluster_registry_enabled:
        from scout.db.cluster_registry import fetch_cluster_registry
        clients_with_data = {k[0] for k in sov_index}
        for client in clients_cfg:
            cid = client["client_id"]
            if cid in clients_with_data:
                continue
            registry = fetch_cluster_registry(sb, cid)
            for cluster_id, meta in registry.items():
                if not cluster_id:
                    continue
                extra_clusters.setdefault(cid, set()).add(cluster_id)
                if (cid, cluster_id) not in cluster_labels:
                    cluster_labels[(cid, cluster_id)] = meta.get("cluster_label") or cluster_id

    today = str(date.today())
    result_clients: list[dict] = []
    for client in clients_cfg:
        client_id = client["client_id"]
        name_lower_to_canonical = {
            c["competitor_name"].lower(): c["competitor_name"]
            for c in client["competitors"] if c.get("competitor_name")
        }
        cluster_ids = sorted({k[1] for k in sov_index if k[0] == client_id} | extra_clusters.get(client_id, set()))
        result_clusters: list[dict] = []
        for cluster_id in cluster_ids:
            # Resolve each sov_weekly entity to a configured competitor by fuzzy name match (exact -> suffix-tolerant
            # substring) so the data's 'RSM' lines up with the configured 'RSM US LLP'. First match per competitor wins.
            resolved: dict[str, list] = {}
            entity_names = sorted({
                k[2] for k in sov_index
                if k[0] == client_id and k[1] == cluster_id and k[2] != "__client__"
            })
            for ename in entity_names:
                canonical = _match_competitor(ename, name_lower_to_canonical)
                if canonical and canonical not in resolved:
                    resolved[canonical] = sov_index[(client_id, cluster_id, ename)]
            result_competitors: list[dict] = []
            for comp in client["competitors"]:
                entries = resolved.get(comp["competitor_name"], [])
                sov_now = entries[0]["sov_score"] if entries else 0.0
                history = entries[1:7]   # R4-2: retain up to 6 prior weeks (within SOV_LOOKBACK_WEEKS=6)
                result_competitors.append({
                    "competitor_name": comp["competitor_name"],
                    "competitor_domain": comp["competitor_domain"],
                    "sov_this_week": sov_now,
                    "sov_history": list(reversed(history)),
                    "consecutive_weeks": _consecutive_week_count(entries),   # R4-2: feeds R4-1 graduation
                })
            client_entries = sov_index.get((client_id, cluster_id, "__client__"), [])
            client_sov_now = client_entries[0]["sov_score"] if client_entries else 0.0
            client_history = list(reversed(client_entries[1:7]))   # R4-2: retain up to 6 prior weeks
            result_clusters.append({
                "cluster_id": cluster_id,
                "cluster_label": cluster_labels.get((client_id, cluster_id), cluster_id),
                "client_sov_this_week": client_sov_now,
                "client_sov_history": client_history,
                "competitors": result_competitors,
            })
        result_clients.append({
            "client_id": client_id,
            "client_name": client["client_name"],
            "company_domain": client.get("company_domain", ""),
            "company_website": client.get("company_website", ""),
            "clusters": result_clusters,
        })

    return {"sync_date": today, "clients": result_clients}
