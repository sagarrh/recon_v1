from __future__ import annotations

import hashlib
from datetime import date
from typing import Any
from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from aivc.measurement.models import (
    IsolationHealth,
    MeasurementFoundationHealth,
    SourceHealth,
    SyncHealth,
)

_SOURCE_TABLES = ("user_metrics", "gsc_query_page_metrics", "ga4_metrics")
_DERIVED_TABLES = (
    "aivc_action_executions",
    "ai_visibility_measurement_plans",
    "ai_visibility_measurement_snapshots",
    "ai_visibility_measurement_outcomes",
)


def _fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _lag_days(latest: date | None, today: date) -> int | None:
    return (today - latest).days if latest is not None else None


def _resolve_client_row(
    cursor: Any,
    *,
    client_id: UUID | None,
    company_name: str | None,
) -> dict[str, Any]:
    if (client_id is None) == (company_name is None):
        raise ValueError("Provide exactly one of client_id or company_name.")
    if client_id is not None:
        rows = cursor.execute(
            """
            select c.client_id, c.client_name, c.company_domain,
                   um.gsc_site_url, um.ga4_property_id,
                   nullif(upper(btrim(o.currency)), '') as currency_code
            from public.clients c
            left join public.user_metrics um on um.client_id = c.client_id
            left join public.onboarding o on o.client_id = c.client_id
            where c.client_id = %s
            """,
            (client_id,),
        ).fetchall()
    else:
        rows = cursor.execute(
            """
            select c.client_id, c.client_name, c.company_domain,
                   um.gsc_site_url, um.ga4_property_id,
                   nullif(upper(btrim(o.currency)), '') as currency_code
            from public.clients c
            left join public.user_metrics um on um.client_id = c.client_id
            left join public.onboarding o on o.client_id = c.client_id
            where lower(btrim(c.client_name)) = lower(btrim(%s))
            order by c.client_id
            """,
            (company_name,),
        ).fetchall()
    if not rows:
        raise LookupError(f"No client was found for {client_id or company_name}.")
    identities = {str(row["client_id"]) for row in rows}
    if len(identities) > 1:
        raise LookupError(
            f"Company '{company_name}' is ambiguous across {len(identities)} clients; "
            "use --client-id."
        )
    return dict(rows[0])


def _sync_health(cursor: Any, state_key: str) -> SyncHealth:
    row = cursor.execute(
        """
        select status, last_synced_at, updated_at, error_message
        from public.sync_state
        where table_name = %s
        """,
        (state_key,),
    ).fetchone()
    if row is None:
        return SyncHealth()
    return SyncHealth(
        status=row["status"],
        last_synced_at=row["last_synced_at"],
        heartbeat_at=row["updated_at"],
        error=row["error_message"],
    )


def _isolation_health(cursor: Any) -> IsolationHealth:
    tables = [*_SOURCE_TABLES, *_DERIVED_TABLES]
    rows = cursor.execute(
        """
        select table_rel.relname as table_name, table_rel.relrowsecurity as rls_enabled
        from pg_class table_rel
        join pg_namespace namespace on namespace.oid = table_rel.relnamespace
        where namespace.nspname = 'public' and table_rel.relname = any(%s)
        """,
        (tables,),
    ).fetchall()
    rls = {str(row["table_name"]): bool(row["rls_enabled"]) for row in rows}
    policies = cursor.execute(
        """
        select tablename, cmd
        from pg_policies
        where schemaname = 'public' and tablename = any(%s)
        """,
        (tables,),
    ).fetchall()
    policy_commands: dict[str, set[str]] = {}
    for row in policies:
        policy_commands.setdefault(str(row["tablename"]), set()).add(str(row["cmd"]).upper())

    source_server_only = all(rls.get(table, False) for table in _SOURCE_TABLES) and all(
        not policy_commands.get(table, set()).intersection({"ALL", "SELECT"})
        for table in _SOURCE_TABLES
    )
    derived_tenant_scoped = all(rls.get(table, False) for table in _DERIVED_TABLES) and all(
        "SELECT" in policy_commands.get(table, set()) or "ALL" in policy_commands.get(table, set())
        for table in _DERIVED_TABLES
    )
    derived_backend_write_only = all(
        not policy_commands.get(table, set()).intersection({"ALL", "INSERT", "UPDATE", "DELETE"})
        for table in _DERIVED_TABLES
    )
    warnings: list[str] = []
    if not source_server_only:
        warnings.append("source_metric_tables_not_server_only")
    if not derived_tenant_scoped:
        warnings.append("measurement_tables_not_tenant_scoped")
    if not derived_backend_write_only:
        warnings.append("measurement_tables_allow_client_writes")
    return IsolationHealth(
        ready=source_server_only and derived_tenant_scoped and derived_backend_write_only,
        source_tables_server_only=source_server_only,
        derived_tables_tenant_scoped=derived_tenant_scoped,
        derived_tables_backend_write_only=derived_backend_write_only,
        rls_enabled_tables=sorted(table for table, enabled in rls.items() if enabled),
        warnings=warnings,
    )


def audit_measurement_foundation(
    settings: Settings,
    *,
    client_id: UUID | None = None,
    company_name: str | None = None,
    today: date | None = None,
    gsc_max_lag_days: int = 4,
    ga4_max_lag_days: int = 3,
) -> MeasurementFoundationHealth:
    """Audit one client's mirrored GSC/GA4 readiness without exposing handles."""
    observed_on = today or date.today()
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        client = _resolve_client_row(
            cursor,
            client_id=client_id,
            company_name=company_name,
        )
        gsc_handle = str(client.get("gsc_site_url") or "").strip() or None
        ga4_handle = str(client.get("ga4_property_id") or "").strip() or None

        gsc = cursor.execute(
            """
            select count(*) as row_count,
                   min(metric_date) as earliest_date,
                   max(metric_date) as latest_date
            from public.gsc_query_page_metrics
            where site_url = %s
            """,
            (gsc_handle,),
        ).fetchone()
        ga4 = cursor.execute(
            """
            select count(*) as row_count,
                   min(metric_date) as earliest_date,
                   max(metric_date) as latest_date,
                   count(*) filter (where conversions > 0) as rows_with_conversions,
                   count(*) filter (where revenue > 0) as rows_with_revenue
            from public.ga4_metrics
            where property_id = %s
            """,
            (ga4_handle,),
        ).fetchone()
        shared = cursor.execute(
            """
            select
              count(*) filter (where gsc_site_url = %s) as gsc_clients,
              count(*) filter (where ga4_property_id = %s) as ga4_clients
            from public.user_metrics
            """,
            (gsc_handle, ga4_handle),
        ).fetchone()
        duplicates = cursor.execute(
            """
            select
              (select count(*) from (
                 select 1
                 from public.gsc_query_page_metrics
                 where site_url = %s
                 group by site_url, metric_date, query, page, country, device
                 having count(*) > 1
               ) duplicate_gsc) as gsc_duplicate_keys,
              (select count(*) from (
                 select 1
                 from public.ga4_metrics
                 where property_id = %s
                 group by property_id, metric_date, source, medium, campaign,
                          landing_page, country, device
                 having count(*) > 1
               ) duplicate_ga4) as ga4_duplicate_keys
            """,
            (gsc_handle, ga4_handle),
        ).fetchone()
        gsc_sync = _sync_health(cursor, "gsc_query_page_metrics")
        ga4_sync = _sync_health(cursor, "ga4_metrics")
        isolation = _isolation_health(cursor)

    if gsc is None or ga4 is None or shared is None or duplicates is None:
        raise RuntimeError("Measurement readiness query returned an incomplete result.")
    gsc_lag = _lag_days(gsc["latest_date"], observed_on)
    ga4_lag = _lag_days(ga4["latest_date"], observed_on)
    gsc_health = SourceHealth(
        source="gsc",
        connected=gsc_handle is not None,
        handle_fingerprint=_fingerprint(gsc_handle),
        clients_sharing_handle=int(shared["gsc_clients"] or 0),
        duplicate_natural_keys=int(duplicates["gsc_duplicate_keys"] or 0),
        row_count=int(gsc["row_count"] or 0),
        earliest_date=gsc["earliest_date"],
        latest_date=gsc["latest_date"],
        lag_days=gsc_lag,
        fresh=gsc_lag is not None and gsc_lag <= gsc_max_lag_days,
        sync=gsc_sync,
    )
    ga4_health = SourceHealth(
        source="ga4",
        connected=ga4_handle is not None,
        handle_fingerprint=_fingerprint(ga4_handle),
        clients_sharing_handle=int(shared["ga4_clients"] or 0),
        duplicate_natural_keys=int(duplicates["ga4_duplicate_keys"] or 0),
        row_count=int(ga4["row_count"] or 0),
        earliest_date=ga4["earliest_date"],
        latest_date=ga4["latest_date"],
        lag_days=ga4_lag,
        fresh=ga4_lag is not None and ga4_lag <= ga4_max_lag_days,
        rows_with_conversions=int(ga4["rows_with_conversions"] or 0),
        rows_with_revenue=int(ga4["rows_with_revenue"] or 0),
        sync=ga4_sync,
    )

    warnings: list[str] = []
    warnings.extend(isolation.warnings)
    for source in (gsc_health, ga4_health):
        if not source.connected:
            warnings.append(f"{source.source}_not_connected")
        elif source.row_count == 0:
            warnings.append(f"{source.source}_no_mirrored_rows")
        elif not source.fresh:
            warnings.append(f"{source.source}_data_stale")
        if source.clients_sharing_handle > 1:
            warnings.append(f"{source.source}_handle_shared_across_clients")
        if source.duplicate_natural_keys > 0:
            warnings.append(f"{source.source}_duplicate_natural_keys")
        if source.sync.status not in (None, "ok", "capped"):
            warnings.append(f"{source.source}_sync_{source.sync.status}")
        if source.sync.status is None:
            warnings.append(f"{source.source}_sync_state_missing")
    currency = client.get("currency_code")
    if not currency:
        warnings.append("revenue_currency_missing")
    if ga4_health.rows_with_revenue == 0:
        warnings.append("ga4_recorded_revenue_missing")

    ready_gsc = bool(
        gsc_health.connected
        and gsc_health.row_count > 0
        and gsc_health.fresh
        and gsc_health.clients_sharing_handle == 1
        and gsc_health.duplicate_natural_keys == 0
        and gsc_health.sync.status in ("ok", "capped")
        and isolation.ready
    )
    ready_ga4 = bool(
        ga4_health.connected
        and ga4_health.row_count > 0
        and ga4_health.fresh
        and ga4_health.clients_sharing_handle == 1
        and ga4_health.duplicate_natural_keys == 0
        and ga4_health.sync.status in ("ok", "capped")
        and isolation.ready
    )
    ready_revenue = bool(ready_ga4 and ga4_health.rows_with_revenue and currency)
    return MeasurementFoundationHealth(
        client_id=UUID(str(client["client_id"])),
        company_name=str(client["client_name"]),
        currency_code=str(currency) if currency else None,
        gsc=gsc_health,
        ga4=ga4_health,
        isolation=isolation,
        ready_for_gsc_measurement=ready_gsc,
        ready_for_ga4_measurement=ready_ga4,
        ready_for_recorded_revenue=ready_revenue,
        warnings=warnings,
    )
