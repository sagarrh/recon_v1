# verify.py — Tier 3: content-aware verified-live checks (FR-VERIFY). Read-only probes via the
# existing ai_access/geo_cache paths; positive evidence only — unreachable/ambiguous NEVER verifies.
# Success also flips the Tier-2 registry fields (asset_status='published', content_url=target) so
# the asset enters Tier-2's attribution pools; a unique-index collision downgrades to a
# content_tracking_url link (the URL already belongs to a Phase-B 'existing' row).
import logging
from datetime import date
from urllib.parse import urlsplit

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)


def _domain(target: str) -> str:
    try:
        return urlsplit(target or "").netloc or (target or "")
    except (ValueError, AttributeError):
        return target or ""


def verify_asset(asset: dict, *, fetch_fn=None, schema_fn=None) -> dict:
    cls = asset.get("asset_type", "")
    if cls in ("llms_txt", "ai_bots_allowlist", "robots_txt"):
        if fetch_fn is None:
            from scout.integrations.ai_access import fetch_ai_access_files
            fetch_fn = fetch_ai_access_files
        result = fetch_fn(_domain(asset.get("target", "")))
        if result is None:
            return {"verified": False, "reason": "domain unreachable", "evidence": {}}
        key = "llms_txt" if cls == "llms_txt" else "ai_bots"
        live = bool(result.get(key))
        return {"verified": live,
                "reason": "" if live else f"{key} not detected on live site",
                "evidence": result}
    if cls in ("schema_jsonld", "faq_page"):
        if schema_fn is None:
            from scout.db.geo_cache import get_schema_types
            schema_fn = get_schema_types
        expected = set((asset.get("content_provenance") or {}).get("schema_types") or [])
        if not expected:
            return {"verified": False, "reason": "no expected schema types recorded", "evidence": {}}
        live = schema_fn(asset.get("client_id", ""))
        if live is None:
            return {"verified": False, "reason": "schema snapshot unavailable (snapshot_stale_or_absent)",
                    "evidence": {}}
        live_set = {str(t).strip() for t in live}
        missing = expected - live_set
        if missing:
            return {"verified": False,
                    "reason": f"types still missing: {', '.join(sorted(missing))} (snapshot_stale_or_absent)",
                    "evidence": {"live_types": sorted(live_set), "expected": sorted(expected)}}
        return {"verified": True, "reason": "",
                "evidence": {"live_types": sorted(live_set), "expected": sorted(expected)}}
    return {"verified": False, "reason": f"unverifiable asset_type: {cls}", "evidence": {}}


def run_verification(sb, cfg, *, fetch_fn=None, schema_fn=None) -> dict:
    if not getattr(cfg, "builder_verify_enabled", False):
        return {"verified": 0, "verify_failed": 0, "skipped": 0}
    from scout.db import asset_writer as aw
    try:
        rows = (sb.table(m.SCOUT_ASSETS_TABLE).select("*")
                .in_("lifecycle_status", ["handed_off", "verify_failed"]).execute().data or [])
    except Exception as e:
        log.warning("[verify] asset fetch failed: %s", e)
        return {"verified": 0, "verify_failed": 0, "skipped": 0}

    counts = {"verified": 0, "verify_failed": 0, "skipped": 0}
    max_cycles = int(getattr(cfg, "asset_verification_max_cycles", 4))
    for asset in rows:
        result = verify_asset(asset, fetch_fn=fetch_fn, schema_fn=schema_fn)
        prior = ((asset.get("verify_evidence") or {}).get("attempts") or [])
        attempts = [*prior, {"verified": result["verified"], "reason": result["reason"]}]
        evidence = {"attempts": attempts, "last": result["evidence"]}
        if result["verified"]:
            # verify_failed rows must pass back through handed_off before verified (state machine).
            if asset.get("lifecycle_status") == "verify_failed":
                ok_hop, hop_reason = aw.transition_asset(
                    sb, asset["id"], "handed_off",
                    client_approval_required=getattr(cfg, "builder_client_approval_required", True))
                if not ok_hop:
                    counts["skipped"] += 1
                    print(f"  re-handoff refused for {asset.get('id')}: {hop_reason}")
                    continue
            extra = {"verify_evidence": evidence, "verify_reason": None,
                     "asset_status": "published", "published_date": str(date.today()),
                     "content_url": asset.get("target")}
            ok, reason = aw.transition_asset(sb, asset["id"], "verified", extra_patch=extra)
            if not ok and reason == "patch failed":
                # Unique-index collision on content_url: link instead of duplicating (FR-VERIFY-6).
                extra_fallback = {k: v for k, v in extra.items() if k != "content_url"}
                extra_fallback["content_tracking_url"] = asset.get("target")
                ok, reason = aw.transition_asset(sb, asset["id"], "verified", extra_patch=extra_fallback)
            if ok:
                counts["verified"] += 1
                print(f"  verified: {asset.get('asset_type')} {asset.get('id')}")
            else:
                counts["skipped"] += 1
                print(f"  verify transition refused for {asset.get('id')}: {reason}")
            continue
        # failure path
        if asset.get("lifecycle_status") == "handed_off":
            aw.transition_asset(sb, asset["id"], "verify_failed",
                                extra_patch={"verify_evidence": evidence, "verify_reason": result["reason"]})
        else:
            aw.patch_scout_asset(sb, asset["id"],
                                 {"verify_evidence": evidence, "verify_reason": result["reason"]})
        counts["verify_failed"] += 1
        if len(attempts) >= max_cycles:
            print(f"  NOT-LIVE WARNING: asset {asset.get('id')} ({asset.get('asset_type')}) has failed "
                  f"verification {len(attempts)}x — reason: {result['reason']}")
    return counts
