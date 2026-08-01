import logging
from datetime import UTC

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)

WINDOW_SENTINEL = "0001-01-01"   # "no measured window yet" — never a real outcome date


def _merge_scout_asset_row(sb, row: dict) -> bool:
    """Cache-independent merge for one scout_assets row: select by the dedupe quad, then patch or insert.
    Fallback for PostgREST 42P10 (stale schema cache after the dedupe-index swap — live E2E, 2026-07-04)."""
    try:
        q = (sb.table(m.SCOUT_ASSETS_TABLE).select("id")
             .eq("client_id", str(row.get("client_id")))
             .eq("asset_type", row.get("asset_type"))
             .eq("content_url", row.get("content_url") or ""))
        rec_id = row.get("recommendation_id")
        q = q.eq("recommendation_id", str(rec_id)) if rec_id else q.is_("recommendation_id", "null")
        existing = q.limit(1).execute().data or []
        if existing:
            sb.table(m.SCOUT_ASSETS_TABLE).update(row).eq("id", existing[0]["id"]).execute()
        else:
            sb.table(m.SCOUT_ASSETS_TABLE).insert(row).execute()
        return True
    except Exception as e:
        log.error("[asset_writer] scout_assets manual merge FAILED for %s/%s: %s",
                  row.get("recommendation_id"), row.get("asset_type"), e)
        return False


def write_scout_assets(sb, rows: list[dict]) -> int:
    """Upsert scout_assets rows on (client_id, recommendation_id, asset_type, content_url).
    None content_url is coalesced to '' (matches the plain dedupe index). A 42P10 (PostgREST's
    schema cache not yet aware of the dedupe index) degrades to a per-row select-then-merge so
    a stale cache never loses registrations. Returns count / 0-empty / -1 when nothing landed."""
    if not rows:
        return 0
    payload = []
    for r in rows:
        r = dict(r)
        if r.get("content_url") is None:
            r["content_url"] = ""
        payload.append(r)
    try:
        sb.table(m.SCOUT_ASSETS_TABLE).upsert(
            payload, on_conflict="client_id,recommendation_id,asset_type,content_url"
        ).execute()
        return len(payload)
    except Exception as e:
        if "42P10" not in str(e):
            log.error("[asset_writer] scout_assets upsert FAILED (%d rows lost): %s", len(payload), e, exc_info=True)
            return -1
        log.warning("[asset_writer] upsert hit 42P10 (stale PostgREST schema cache) — "
                    "falling back to per-row merge; run NOTIFY pgrst,'reload schema' to restore fast path")
        merged = sum(1 for r in payload if _merge_scout_asset_row(sb, r))
        return merged if merged else -1


def write_asset_attribution(sb, rows: list[dict]) -> int:
    """Upsert scout_asset_attribution rows on (scout_asset_id, revenue_source, window_start, window_end).
    Display-only keys (leading underscore) are stripped so builder records can carry CLI context without
    breaking the PostgREST column check. Re-running is idempotent. Returns count / 0-empty / -1-error."""
    if not rows:
        return 0
    payload = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    # Windowless (Phase-A) rows use a sentinel date instead of NULL: under a standard unique
    # index NULLs are distinct, so NULL windows silently duplicate on every re-run (live E2E).
    for r in payload:
        if r.get("window_start") is None:
            r["window_start"] = WINDOW_SENTINEL
        if r.get("window_end") is None:
            r["window_end"] = WINDOW_SENTINEL
    try:
        sb.table(m.SCOUT_ASSET_ATTRIBUTION_TABLE).upsert(
            payload, on_conflict="scout_asset_id,revenue_source,window_start,window_end"
        ).execute()
        return len(payload)
    except Exception as e:
        log.error("[asset_writer] scout_asset_attribution upsert FAILED (%d rows lost): %s", len(payload), e, exc_info=True)
        return -1


# ---- Tier 3: builder lifecycle writers (patch-by-id, NEVER upsert an existing row — see D2/D9) ----

def patch_scout_asset(sb, asset_id: str, patch: dict) -> bool:
    """Update one scout_assets row by id. The ONLY legal mutation path for existing rows:
    the dedupe index uses COALESCE(content_url,''), so upserting a changed content_url would
    mint a duplicate instead of updating. Returns False on any error (logged, never raised)."""
    if not asset_id or not patch:
        return False
    try:
        sb.table(m.SCOUT_ASSETS_TABLE).update(patch).eq("id", asset_id).execute()
        return True
    except Exception as e:
        log.error("[asset_writer] patch_scout_asset(%s) FAILED: %s", asset_id, e, exc_info=True)
        return False


def insert_build_briefs(sb, rows: list[dict]) -> int:
    """Upsert scout_build_briefs on (recommendation_id, asset_class, target) — FR-BRIEF-6 idempotency.
    Returns count / 0-empty / -1-error."""
    if not rows:
        return 0
    try:
        # ignore_duplicates: an existing brief keeps its lifecycle status — re-running intake
        # must never regress a generated/superseded brief back to 'drafted' (HIGH-4, live E2E).
        sb.table(m.SCOUT_BUILD_BRIEFS_TABLE).upsert(
            rows, on_conflict="recommendation_id,asset_class,target", ignore_duplicates=True
        ).execute()
        return len(rows)
    except Exception as e:
        log.error("[asset_writer] build_briefs upsert FAILED (%d rows lost): %s", len(rows), e, exc_info=True)
        return -1


def update_build_brief(sb, brief_id: str, patch: dict) -> bool:
    if not brief_id or not patch:
        return False
    try:
        sb.table(m.SCOUT_BUILD_BRIEFS_TABLE).update(patch).eq("id", brief_id).execute()
        return True
    except Exception as e:
        log.error("[asset_writer] update_build_brief(%s) FAILED: %s", brief_id, e, exc_info=True)
        return False


def insert_asset_approval(sb, row: dict) -> bool:
    """APPEND-ONLY ledger insert (FR-APPROVE-4). Refuses without a reviewer identity — the human
    gate must be attributable. Plain INSERT: every decision is a new row, nothing is deduped."""
    tbl = sb.table(m.SCOUT_ASSET_APPROVALS_TABLE)
    if not row.get("reviewer"):
        log.warning("[asset_writer] approval refused: no reviewer identity")
        return False
    try:
        tbl.insert(row).execute()
        return True
    except Exception as e:
        log.error("[asset_writer] approval insert FAILED: %s", e, exc_info=True)
        return False


def fetch_asset_approvals(sb, asset_id: str) -> list[dict]:
    try:
        return (sb.table(m.SCOUT_ASSET_APPROVALS_TABLE)
                .select("gate,decision,reviewer,asset_revision,decided_at")
                .eq("scout_asset_id", str(asset_id)).execute().data or [])
    except Exception as e:
        log.warning("[asset_writer] approvals fetch for %s failed: %s", asset_id, e)
        return []


def transition_asset(sb, asset_id: str, new_status: str, *,
                     client_approval_required: bool = True,
                     extra_patch: dict | None = None) -> tuple[bool, str]:
    """Enforced lifecycle transition: read the row + its approval ledger, consult the pure state
    machine, then patch. Zero writes on an illegal transition. Timestamps stamped here so callers
    cannot forge them; needs_edit -> generated bumps asset_revision (a new generation revision)."""
    from datetime import datetime

    from scout.builders.lifecycle import can_transition
    try:
        resp = (sb.table(m.SCOUT_ASSETS_TABLE)
                .select("id,lifecycle_status,asset_revision")
                .eq("id", str(asset_id)).limit(1).execute())
        rows = resp.data or []
    except Exception as e:
        return False, f"asset fetch failed: {e}"
    if not rows:
        return False, f"asset not found: {asset_id}"
    current = rows[0].get("lifecycle_status")
    approvals = fetch_asset_approvals(sb, asset_id)
    ok, reason = can_transition(current, new_status, approvals=approvals,
                                client_approval_required=client_approval_required)
    if not ok:
        return False, reason
    patch: dict = {"lifecycle_status": new_status}
    now = datetime.now(UTC).isoformat()
    if new_status == "handed_off":
        patch["handed_off_at"] = now
    if new_status == "verified":
        patch["verified_at"] = now
    if current == "needs_edit" and new_status == "generated":
        patch["asset_revision"] = int(rows[0].get("asset_revision") or 1) + 1
    if extra_patch:
        extra = {k: v for k, v in extra_patch.items() if k != "lifecycle_status"}
        patch.update(extra)
    if not patch_scout_asset(sb, asset_id, patch):
        return False, "patch failed"
    return True, ""
