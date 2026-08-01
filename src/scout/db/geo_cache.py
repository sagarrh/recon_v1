# geo_cache.py — Read-only accessors for GEO scraped-content + website-files + schema (via FDW foreign tables).
# Purpose: Let Scout reuse GEO's company_scraped_data_cache (per-domain) and report_data website/schema (per-client).
# Scope: Stateless Supabase reads scoped by domain (url ILIKE) / client_id; never writes. Returns None/[] on miss.
# Consumers: scout/db/cache.py re-exports these; website_diff uses get_scraped_page to skip Bright Data when fresh.
import logging
from datetime import UTC, datetime, timedelta

from scout.db import sed_mapping as m
from scout.utils import sb as _sb

log = logging.getLogger(__name__)


def get_scraped_page(domain: str, ttl_days: int) -> dict | None:
    """Return {url, content, scraped_at} for a domain from GEO company_scraped_data_cache when fresh, else None.
    URL-keyed shared cache scoped by url ILIKE '%domain%'; rejects rows older than ttl_days by last_refreshed."""
    if not domain:
        return None
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=ttl_days)).isoformat()
        resp = (
            _sb().table(m.COMPANY_SCRAPED_CACHE_TABLE)
            .select("url,source,last_refreshed")
            .ilike("url", f"%{domain}%")
            .gte("last_refreshed", cutoff)
            .order("last_refreshed", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = rows[0]
        source = row.get("source")
        if not source:
            return None
        return {
            "url": row.get("url") or domain,
            "content": source,
            "scraped_at": row.get("last_refreshed") or "",
        }
    except Exception as e:
        log.warning("[geo_cache] get_scraped_page(%s) failed: %s", domain, e)
        return None


def get_website_files(client_id_text: str) -> dict | None:
    """Return the latest report_data.website_files_data (robots_txt + ai_bots map + llm_txt) for a client, else None.
    CLIENT-scoped; preserves a present-but-empty {} snapshot as a distinct 'checked, no files' signal (no `or None`) so client_readiness trusts it instead of redundantly live-scraping."""
    return _latest_report_json(client_id_text, m.REPORT_DATA_COLS["website_files_data"])


def get_schema_types(client_id_text: str) -> list[str] | None:
    """Return JSON-LD @type values from the latest report_data.schema_data for a client (client's own site), else None.
    CLIENT-scoped; reads a 'found'/'types' list or dict keys defensively; returns None when no report/schema present."""
    data = _latest_report_json(client_id_text, m.REPORT_DATA_COLS["schema_data"])
    if not isinstance(data, dict):
        return None
    found = data.get("found") or data.get("types") or data.get("schema_types")
    if isinstance(found, list):
        out = sorted({str(t) for t in found if t})
        return out or None
    if isinstance(found, dict):
        out = sorted({str(k) for k in found if k})
        return out or None
    return None


def _latest_report_json(client_id_text: str, column: str):
    """Fetch one JSON column from the most recent report_data row for a client and return it parsed, else None.
    Returns the decoded object (dict/list) or None on miss/parse failure; client_id is matched as TEXT."""
    if not client_id_text:
        return None
    try:
        resp = (
            _sb().table(m.REPORT_DATA_TABLE)
            .select(f"{column},{m.REPORT_DATA_COLS['created_at']}")
            .eq(m.REPORT_DATA_COLS["client_id"], str(client_id_text))
            .order(m.REPORT_DATA_COLS["created_at"], desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        value = rows[0].get(column)
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            import json
            try:
                return json.loads(value)
            except Exception:
                return None
        return None
    except Exception as e:
        log.warning("[geo_cache] _latest_report_json(%s,%s) failed: %s", client_id_text, column, e)
        return None
