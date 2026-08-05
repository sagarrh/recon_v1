"""Read the mirrored Google Search Console table for one client, window and target set.

The data is already in this database — scout-sync mirrors `public.gsc_query_page_metrics` from the
GEO platform. So no Google API is called here, no OAuth is handled, and no refresh token is ever
read: this module selects an explicit column allowlist and nothing else.

Tenancy is the real hazard. GSC rows are keyed by `site_url`, not by client, so every read is scoped
by a `TenantScope` that has already established this client may see that handle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.measurement.mapping import normalize_page
from ai_visibility.measurement.tenancy import TenantScope, host_matches_owned

# Explicit allowlist. `user_metrics` holds refresh tokens; nothing here touches it beyond the
# handle already resolved in TenantScope, and no `select *` is ever issued against these tables.
GSC_COLUMNS = ("metric_date", "query", "page", "clicks", "impressions", "ctr", "position")

_SELECT = """
    select metric_date, query, page, clicks, impressions, ctr, position
      from public.gsc_query_page_metrics
     where site_url = %s
       and metric_date >= %s
       and metric_date <= %s
"""


@dataclass(frozen=True)
class GscWindowRows:
    """Rows for one window, plus why they might be missing."""

    rows: list[dict[str, Any]]
    source_status: str
    warnings: list[str]
    effective_start: date | None
    effective_end: date | None

    @property
    def available(self) -> bool:
        return self.source_status == "available"


def fetch_source_vocabulary(
    settings: Settings, scope: TenantScope, *, start: date, end: date
) -> tuple[list[str], list[str]]:
    """Distinct queries and pages the source holds for this client and window.

    Mapping runs against this rather than against the whole table, so a target is judged against
    what the source could actually return for the window being measured."""
    if not scope.gsc_measurable or scope.gsc_site_url is None:
        return [], []
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(
            """
            select distinct query, page
              from public.gsc_query_page_metrics
             where site_url = %s and metric_date >= %s and metric_date <= %s
            """,
            (scope.gsc_site_url, start, end),
        ).fetchall()
    queries = sorted({str(r["query"]) for r in rows if r.get("query")})
    pages = sorted({str(r["page"]) for r in rows if r.get("page")})
    return queries, pages


def fetch_window(
    settings: Settings,
    scope: TenantScope,
    *,
    start: date,
    end: date,
    matched_queries: list[str],
    matched_pages: list[str],
) -> GscWindowRows:
    """Fetch the rows for one window, restricted to the mapped targets.

    With no mapped target this returns `unmapped` rather than the client's whole site: measuring
    everything would answer a question nobody asked and would move for reasons unrelated to the
    action being assessed."""
    if scope.gsc_status != "ok":
        return GscWindowRows([], scope.gsc_status, list(scope.warnings), None, None)
    if scope.gsc_site_url is None:
        return GscWindowRows([], "not_connected", ["no gsc_site_url for this client"], None, None)
    if not matched_queries and not matched_pages:
        return GscWindowRows(
            [], "unmapped",
            ["no target query or page could be matched in this window"], None, None,
        )

    clauses: list[str] = []
    params: list[Any] = [scope.gsc_site_url, start, end]
    if matched_queries:
        clauses.append("query = any(%s)")
        params.append(matched_queries)
    if matched_pages:
        clauses.append("page = any(%s)")
        params.append(matched_pages)
    sql = f"{_SELECT} and ({' or '.join(clauses)})"

    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = [dict(row) for row in cursor.execute(sql, tuple(params)).fetchall()]

    warnings = list(scope.warnings)

    # When an override permitted a shared handle, domain scoping is the ONLY thing separating this
    # client from the other. Applied here as a hard filter, not a caveat in prose.
    if scope.requires_domain_scoping:
        before = len(rows)
        rows = [
            r for r in rows
            if host_matches_owned(str(r.get("page") or ""), scope.owned_domains)
        ]
        dropped = before - len(rows)
        warnings.append(
            f"shared handle: domain scoping applied, {dropped} of {before} row(s) belonged to "
            "another client on this property"
        )

    if not rows:
        return GscWindowRows([], "empty", warnings, None, None)

    dates = [r["metric_date"] for r in rows if r.get("metric_date")]
    return GscWindowRows(
        rows=rows,
        source_status="available",
        warnings=warnings,
        effective_start=min(dates) if dates else None,
        effective_end=max(dates) if dates else None,
    )


def owned_page_checker(scope: TenantScope) -> Callable[[str], bool]:
    """A predicate for mapping: is this page on a domain the client owns?"""

    def check(value: str) -> bool:
        if not scope.owned_domains:
            return False
        return host_matches_owned(normalize_page(value).split("/")[0], scope.owned_domains)

    return check
