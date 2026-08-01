# sov_compute.py — Derive per-week SOV scores from ai_responses when sov_weekly is empty.
# Purpose: Provides a fallback total_visibility series so SOV-less clients still produce triggers as long as ai_responses has citations data.
# Scope: Pure compute over Supabase ai_responses rows; in-memory only — does not write back to sov_weekly.
# Consumers: scout/db/reader.py wires this in after the sov_weekly fetch for any (client_id, cluster_id) pair returning zero rows.
import logging
from collections import defaultdict
from datetime import date, timedelta

from ai_visibility.utils.text import literal_company_mentions
from scout.config import get_config
from scout.db import sed_mapping as m
from scout.utils import normalize_domain as _normalise_domain
from scout.utils import parse_json_cell as _parse_jsonish

log = logging.getLogger(__name__)


def compute_sov_from_ai(
    sb,
    client_id: str,
    cluster_id: str,
    competitor_names: list[str],
    competitor_domains: dict[str, str],
    lookback_weeks: int,
    client_name: str = "",
) -> list[dict]:
    """Derive a flat list of {"client_id","cluster_id","entity_name","week","sov_score"} from ai_responses for one cluster, ready to merge into reader.sov_index.
    sov_score is the entity's mention share within (cluster, week) as percentage-points (0-100, ×100) to match sov_weekly's total_visibility and the pp severity floors."""
    cutoff = str(date.today() - timedelta(weeks=lookback_weeks))
    try:
        resp = (
            sb.table(m.AI_MONITORING_TABLE)
            .select("cluster_id,cluster_name,week_date,citations_data,answers_list,synced_at")
            .eq(m.AI_MONITORING_COLS["client_id"], client_id)
            .eq(m.AI_MONITORING_COLS["cluster_id"], cluster_id)
            .gte("week_date", cutoff)
            .limit(500)
            .execute()
        )
    except Exception as e:
        log.warning(
            "[sov_compute] ai_responses fetch for client=%s cluster=%s failed: %s",
            client_id,
            cluster_id,
            e,
        )
        return []
    rows = resp.data or []
    if not rows:
        return []

    by_week: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        wk = str(row.get("week_date") or "")
        if not wk:
            continue
        by_week[wk].append(row)

    name_lower_to_canonical: dict[str, str] = {n.lower(): n for n in competitor_names if n}
    domain_to_name: dict[str, str] = {}
    for name, dom in competitor_domains.items():
        if not dom:
            continue
        dom_norm = _normalise_domain(dom)
        if dom_norm:
            domain_to_name[dom_norm] = name

    out: list[dict] = []
    for week, week_rows in by_week.items():
        domain_counts: dict[str, float] = defaultdict(float)
        text_blob_lower = []
        for r in week_rows:
            cd = _parse_jsonish(r.get("citations_data"))
            if isinstance(cd, dict):
                for raw_domain, payload in cd.items():
                    if not raw_domain or not isinstance(payload, dict):
                        continue
                    try:
                        cnt = float(payload.get("count", 0) or 0)
                    except (TypeError, ValueError):
                        cnt = 0.0
                    if cnt > 0:
                        domain_counts[_normalise_domain(raw_domain)] += cnt
            al = _parse_jsonish(r.get("answers_list"))
            if isinstance(al, list):
                for ans in al:
                    if isinstance(ans, str) and ans.strip():
                        text_blob_lower.append(ans.lower())

        entity_counts: dict[str, float] = defaultdict(float)
        for dom, cnt in domain_counts.items():
            mapped = domain_to_name.get(dom)
            if mapped:
                entity_counts[mapped] += cnt

        if text_blob_lower:
            joined = " ".join(text_blob_lower)
            legacy_counts = {
                canonical: joined.count(low) for low, canonical in name_lower_to_canonical.items()
            }
            if client_name:
                legacy_counts["__client__"] = joined.count(client_name.lower())

            aliases_by_company = {name: [name] for name in competitor_names if name}
            if client_name:
                aliases_by_company["__client__"] = [client_name]
            literal_counts: dict[str, int] = defaultdict(int)
            for answer in text_blob_lower:
                for company, count in literal_company_mentions(answer, aliases_by_company).items():
                    literal_counts[company] += count

            cfg = get_config()
            if cfg.sov_literal_matching_shadow_enabled and dict(literal_counts) != legacy_counts:
                log.info(
                    "[sov_compute] mention-counter shadow client=%s cluster=%s week=%s legacy=%s literal=%s",
                    client_id,
                    cluster_id,
                    week,
                    legacy_counts,
                    dict(literal_counts),
                )
            selected_counts = literal_counts if cfg.sov_literal_matching_enabled else legacy_counts
            for canonical in competitor_names:
                if canonical in entity_counts:
                    continue
                mentions = selected_counts.get(canonical, 0)
                if mentions > 0:
                    entity_counts[canonical] = float(mentions)
            if client_name:
                client_mentions = selected_counts.get("__client__", 0)
                if client_mentions > 0:
                    entity_counts["__client__"] = float(client_mentions)

        total = sum(entity_counts.values())
        if total <= 0:
            continue
        for entity, cnt in entity_counts.items():
            out.append(
                {
                    "client_id": client_id,
                    "cluster_id": cluster_id,
                    "entity_name": entity,
                    "week": week,
                    "sov_score": 100.0 * cnt / total,
                }
            )

    log.info(
        "[sov_compute] computed %d entries for client=%s cluster=%s across %d week(s)",
        len(out),
        client_id,
        cluster_id,
        len(by_week),
    )
    return out
