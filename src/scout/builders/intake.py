# intake.py — Tier 3: turn Tier-2 target rows into build briefs (FR-BRIEF; no LLM).
# Purpose: The readiness gaps live ONLY in scout_assets.derived_from (client_readiness is never
#          persisted on recommendations — D4). Intake reads those snapshots + the rec's revenue
#          fields and shapes scout_build_briefs rows.
# Consumers: scripts/build_assets.py (--intake).
import logging
from urllib.parse import urlsplit

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)


def fetch_target_assets(sb, client_id=None) -> list[dict]:
    try:
        q = (sb.table(m.SCOUT_ASSETS_TABLE).select("*")
             .eq("asset_source", "target").eq("asset_status", "proposed"))
        if client_id:
            q = q.eq("client_id", str(client_id))
        rows = q.execute().data or []
        return [r for r in rows if r.get("recommendation_id")]
    except Exception as e:
        log.warning("[intake] target-asset fetch failed: %s", e)
        return []


def fetch_recs_by_id(sb, rec_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(rec_ids), 100):
        chunk = rec_ids[i:i + 100]
        try:
            resp = (sb.table(m.SCOUT_RECOMMENDATIONS_TABLE)
                    .select("id,client_name,cluster_label,priority,validation_status,"
                            "revenue_category,revenue_value_usd")
                    .in_("id", chunk).execute())
            for r in (resp.data or []):
                out[r.get("id")] = r
        except Exception as e:
            log.warning("[intake] recs fetch failed: %s", e)
    return out


def _site_root(url: str) -> str:
    """https://acme.com/crm-software -> https://acme.com (llms.txt/robots live at the origin)."""
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except (ValueError, AttributeError):
        pass
    return ""


_SITE_ROOT_CLASSES = {"llms_txt", "ai_bots_allowlist", "robots_txt"}


def briefs_from_targets(targets: list[dict], recs: dict[str, dict],
                        site_roots: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Pure translation: Tier-2 target rows -> scout_build_briefs rows.
    Returns (briefs, skipped) where each skipped item is {"scout_asset_id", "reason"}."""
    briefs: list[dict] = []
    skipped: list[dict] = []
    seen: set[tuple] = set()

    for t in targets:
        rec = recs.get(t.get("recommendation_id"))
        if rec is None:
            skipped.append({"scout_asset_id": t.get("id"), "reason": "recommendation not found"})
            continue
        if rec.get("validation_status", "ok") != "ok":
            skipped.append({"scout_asset_id": t.get("id"), "reason": "recommendation quarantined"})
            continue

        derived = t.get("derived_from") or {}
        target_url = derived.get("target_url") or ""
        asset_class = t.get("asset_type") or ""
        root = _site_root(target_url) or (site_roots or {}).get(t.get("client_id"), "")

        def _emit(
            cls: str,
            tgt: str,
            *,
            target: dict = t,
            source: dict = derived,
            recommendation: dict = rec,
        ):
            key = (target.get("recommendation_id"), cls, tgt)
            if not tgt:
                skipped.append({"scout_asset_id": target.get("id"),
                                "reason": f"no resolvable target for {cls}"})
                return
            if key in seen:
                return
            seen.add(key)
            briefs.append({
                "recommendation_id": target.get("recommendation_id"),
                "scout_asset_id": target.get("id"),
                "client_id": target.get("client_id"),
                "cluster_id": target.get("cluster_id"),
                "cluster_label": target.get("cluster_label"),
                "asset_class": cls,
                "target": tgt,
                "seed_signals": source,
                "revenue_category": recommendation.get("revenue_category") or "unavailable",
                "revenue_value_usd": recommendation.get("revenue_value_usd"),
                "status": "drafted",
                "run_id": target.get("run_id"),
            })

        if asset_class in _SITE_ROOT_CLASSES:
            _emit(asset_class, root)
        else:
            _emit(asset_class, target_url)

        # An llms_txt gap row whose snapshot also shows ai_bots absent spawns the robots-directive brief.
        if asset_class == "llms_txt" and derived.get("ai_bots_present") is False:
            _emit("ai_bots_allowlist", root)

    return briefs, skipped
