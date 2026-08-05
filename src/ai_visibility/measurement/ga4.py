"""Read the mirrored GA4 table for one client, window and target page set.

A deliberate sibling of `gsc.py`: same three functions, same signatures, same tenancy discipline.
GA4 rows are keyed by `property_id`, which is not a client, so every read is scoped by a
`TenantScope` that has already established this client may see that property.

Scope note: this module reads the ENGAGEMENT layer — sessions, engaged sessions, users. It does not
read conversions or revenue as a measurable outcome, because on the properties observed here key
events are not configured: three conversions in eight months, all on a row whose landing_page is
`(not set)` and therefore attributable to no target page. Reporting that as a commercial result
would dress an instrumentation gap as a finding.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.measurement.mapping import normalize_page
from ai_visibility.measurement.tenancy import TenantScope, host_matches_owned

# Explicit allowlist. `user_metrics` holds refresh tokens; nothing here touches it beyond the
# handle already resolved in TenantScope, and no `select *` is issued against these tables.
GA4_COLUMNS = (
    "metric_date", "landing_page", "source", "medium",
    "sessions", "engaged_sessions", "total_users", "new_users",
)

_SELECT = """
    select metric_date, landing_page, source, medium,
           sessions, engaged_sessions, total_users, new_users
      from public.ga4_metrics
     where property_id = %s
       and metric_date >= %s
       and metric_date <= %s
"""


def _has_host(landing_page: str) -> bool:
    """True when a GA4 landing_page carries a hostname rather than being a bare path.

    Whether it does decides if two clients on one property can be separated at all."""
    value = (landing_page or "").strip()
    if not value:
        return False
    if "://" in value:
        value = value.split("://", 1)[1]
    return not value.startswith("/") and "." in value.split("/")[0]


@dataclass(frozen=True)
class Ga4WindowRows:
    rows: list[dict[str, Any]]
    source_status: str
    warnings: list[str]
    effective_start: date | None
    effective_end: date | None

    @property
    def available(self) -> bool:
        return self.source_status == "available"


def fetch_fresh_through(settings: Settings, scope: TenantScope) -> date | None:
    """Last date this client's GA4 data can be trusted complete: max(metric_date) minus one day.

    The most recent day present is routinely partial, and including it drags the tail of a
    follow-up window down — reading as a decline that has not happened."""
    if not scope.ga4_measurable or scope.ga4_property_id is None:
        return None
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        row = cursor.execute(
            "select max(metric_date) as latest from public.ga4_metrics where property_id = %s",
            (scope.ga4_property_id,),
        ).fetchone()
    latest = (row or {}).get("latest")
    return (latest - timedelta(days=1)) if latest else None


def fetch_source_pages(
    settings: Settings, scope: TenantScope, *, start: date, end: date
) -> list[str]:
    """Distinct landing pages GA4 holds for this client and window.

    `(not set)` is excluded: it is GA4's placeholder for a row with no page attribution, and
    matching a target against it would attribute unrelated activity to that page."""
    if not scope.ga4_measurable or scope.ga4_property_id is None:
        return []
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(
            """
            select distinct landing_page from public.ga4_metrics
             where property_id = %s and metric_date >= %s and metric_date <= %s
               and landing_page is not null and landing_page <> '(not set)'
            """,
            (scope.ga4_property_id, start, end),
        ).fetchall()
    return sorted({str(r["landing_page"]) for r in rows if r.get("landing_page")})


def fetch_window(
    settings: Settings,
    scope: TenantScope,
    *,
    start: date,
    end: date,
    matched_pages: list[str],
) -> Ga4WindowRows:
    """Fetch one window's rows, restricted to the mapped target pages.

    With no mapped page this returns `unmapped` rather than the whole property: measuring every
    page would answer a question nobody asked and would move for unrelated reasons."""
    if scope.ga4_status != "ok":
        return Ga4WindowRows([], scope.ga4_status, list(scope.warnings), None, None)
    if scope.ga4_property_id is None:
        return Ga4WindowRows(
            [], "not_connected", ["no ga4_property_id for this client"], None, None
        )
    if not matched_pages:
        return Ga4WindowRows(
            [], "unmapped", ["no target page could be matched in this window"], None, None
        )

    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = [
            dict(row)
            for row in cursor.execute(
                f"{_SELECT} and landing_page = any(%s)",
                (scope.ga4_property_id, start, end, matched_pages),
            ).fetchall()
        ]

    warnings = list(scope.warnings)

    # Where an override permitted a shared property, domain scoping is the only thing separating
    # this client from the other. Applied as a hard filter, not a caveat in prose.
    if scope.requires_domain_scoping:
        # GA4 stores landing_page as a PATH ('/insights'), carrying no host. On a property shared
        # by two clients that makes them genuinely inseparable: '/pricing' could belong to either.
        # Refuse loudly rather than filter to zero and let it read as "no traffic".
        if not any(_has_host(str(r.get("landing_page") or "")) for r in rows):
            return Ga4WindowRows(
                [], "refused",
                [
                    *warnings,
                    "shared GA4 property and landing_page values carry no hostname, so the two "
                    "clients on this property cannot be told apart; refusing to attribute rather "
                    "than guess. Resolve by giving this client its own GA4 property.",
                ],
                None, None,
            )
        before = len(rows)
        rows = [
            r for r in rows
            if host_matches_owned(str(r.get("landing_page") or ""), scope.owned_domains)
        ]
        warnings.append(
            f"shared property: domain scoping applied, {before - len(rows)} of {before} row(s) "
            "belonged to another client"
        )

    if not rows:
        return Ga4WindowRows([], "empty", warnings, None, None)

    dates = [r["metric_date"] for r in rows if r.get("metric_date")]
    return Ga4WindowRows(
        rows=rows,
        source_status="available",
        warnings=warnings,
        effective_start=min(dates) if dates else None,
        effective_end=max(dates) if dates else None,
    )


def owned_page_checker(scope: TenantScope) -> Callable[[str], bool]:
    """A predicate for mapping: is this landing page on a domain the client owns?"""

    def check(value: str) -> bool:
        if not scope.owned_domains:
            return False
        return host_matches_owned(normalize_page(value).split("/")[0], scope.owned_domains)

    return check
