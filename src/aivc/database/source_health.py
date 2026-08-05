"""Health of the mirrored data this backend depends on but does not own.

Every metric here answers one question: **is a source still actually arriving?**

That question is not the same as "did the sync succeed". `ai_monitoring` reported `status=ok` for
months while writing nothing at all — its query selected a column that exists only in Scout, so
every run failed at the row level and succeeded at the job level. A green sync light hid a table
that had stopped advancing.

So each source is judged on movement, not on self-report: the newest row it holds, how far behind
that is, and whether its heartbeat is consistent with its data. A source whose sync says `ok` while
its data sits still is the specific failure this module exists to catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect

OK = "ok"
STALE = "stale"                  # data exists but has stopped advancing
SILENT = "silent"                # sync claims success while data has not moved
NOT_SYNCED = "not_synced"        # no heartbeat at all
EMPTY = "empty"                  # connected, but nothing has ever arrived

# How far a source may fall behind before it is stale. GSC and GA4 are clamped by Google's own
# reporting delay (2-3 days observed here), so the threshold sits above that: alerting on Google's
# normal lag would train everyone to ignore the alert.
DEFAULT_MAX_LAG_DAYS = 5
# A heartbeat this recent means the sync ran. If data is stale anyway, the sync is running and
# achieving nothing — the exact shape of the ai_monitoring failure.
RECENT_HEARTBEAT_HOURS = 24


@dataclass(frozen=True)
class SourceHealth:
    name: str
    table: str
    status: str
    row_count: int
    latest_data: date | None
    lag_days: int | None
    last_synced_at: datetime | None
    sync_status: str | None
    detail: str
    warnings: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.status == OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "table": self.table,
            "status": self.status,
            "row_count": self.row_count,
            "latest_data": self.latest_data,
            "lag_days": self.lag_days,
            "last_synced_at": self.last_synced_at,
            "sync_status": self.sync_status,
            "detail": self.detail,
            "warnings": self.warnings,
        }


# Each mirrored source: the table, its date column, and the sync_state key that reports on it.
MONITORED_SOURCES = (
    ("gsc", "gsc_query_page_metrics", "metric_date", "gsc_query_page_metrics"),
    ("ga4", "ga4_metrics", "metric_date", "ga4_metrics"),
    ("ai_monitoring", "ai_monitoring", "created_at", "ai_monitoring"),
    ("ai_responses", "ai_responses", "synced_at", "ai_monitoring_mirror"),
    ("sov_weekly", "sov_weekly", "week_date", "cluster_visibility_scores"),
)


def _classify(
    *,
    row_count: int,
    lag_days: int | None,
    last_synced_at: datetime | None,
    sync_status: str | None,
    now: datetime,
    max_lag_days: int,
) -> tuple[str, str]:
    """Grade one source on whether its DATA is moving, not on what its sync reports."""
    if row_count == 0:
        return EMPTY, "no rows have ever arrived in this table"
    if last_synced_at is None:
        return NOT_SYNCED, "no sync heartbeat for this source"
    if lag_days is None:
        return EMPTY, "rows exist but none carry a usable date"

    heartbeat_age = now - last_synced_at
    heartbeat_recent = heartbeat_age <= timedelta(hours=RECENT_HEARTBEAT_HOURS)

    if lag_days <= max_lag_days:
        return OK, f"newest row is {lag_days} day(s) old"

    # The dangerous case: the job keeps succeeding while the data stands still.
    if heartbeat_recent and (sync_status or "").lower() == "ok":
        return SILENT, (
            f"sync reported '{sync_status}' within the last "
            f"{RECENT_HEARTBEAT_HOURS}h but the newest row is {lag_days} day(s) old — "
            "the sync is running and writing nothing"
        )
    return STALE, f"newest row is {lag_days} day(s) old, beyond the {max_lag_days}-day threshold"


_SOURCE_SQL = """
    select count(*) as row_count, max({date_column})::date as latest_data
      from public.{table}
"""

_SYNC_SQL = """
    select table_name, status, last_synced_at
      from public.sync_state
     where table_name = any(%s)
"""


def check_source_health(
    settings: Settings, *, max_lag_days: int = DEFAULT_MAX_LAG_DAYS
) -> list[SourceHealth]:
    """Assess every mirrored source this backend reads."""
    results: list[SourceHealth] = []
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        now_row = cursor.execute("select now() as now, current_date as today").fetchone()
        if now_row is None:
            raise RuntimeError("Database did not return a current timestamp.")
        now: datetime = now_row["now"]
        today: date = now_row["today"]

        sync_keys = [key for *_, key in MONITORED_SOURCES]
        heartbeats = {
            str(row["table_name"]): row
            for row in cursor.execute(_SYNC_SQL, (sync_keys,)).fetchall()
        }

        for name, table, date_column, sync_key in MONITORED_SOURCES:
            warnings: list[str] = []
            try:
                row = cursor.execute(
                    _SOURCE_SQL.format(table=table, date_column=date_column)
                ).fetchone()
            except Exception as exc:
                results.append(
                    SourceHealth(
                        name=name, table=table, status=NOT_SYNCED, row_count=0,
                        latest_data=None, lag_days=None, last_synced_at=None, sync_status=None,
                        detail=f"table unreadable: {type(exc).__name__}",
                    )
                )
                continue

            row_count = int((row or {}).get("row_count") or 0)
            latest = (row or {}).get("latest_data")
            lag_days = (today - latest).days if latest else None
            heartbeat = heartbeats.get(sync_key)
            last_synced_at = heartbeat["last_synced_at"] if heartbeat else None
            sync_status = heartbeat["status"] if heartbeat else None

            status, detail = _classify(
                row_count=row_count, lag_days=lag_days, last_synced_at=last_synced_at,
                sync_status=sync_status, now=now, max_lag_days=max_lag_days,
            )
            if status == SILENT:
                warnings.append(
                    "a green sync status with stalled data is how ai_monitoring failed silently "
                    "for months; check the sync query for a column that does not exist upstream"
                )
            results.append(
                SourceHealth(
                    name=name, table=table, status=status, row_count=row_count,
                    latest_data=latest, lag_days=lag_days, last_synced_at=last_synced_at,
                    sync_status=sync_status, detail=detail, warnings=warnings,
                )
            )
    return results


def summarize_source_health(sources: list[SourceHealth]) -> dict[str, Any]:
    """Roll the per-source results into something an operator can scan."""
    unhealthy = [s for s in sources if not s.healthy]
    return {
        "healthy": not unhealthy,
        "checked": len(sources),
        "by_status": {
            status: sum(1 for s in sources if s.status == status)
            for status in sorted({s.status for s in sources})
        },
        # Named separately because a silently-failing sync is worse than an honestly-failing one:
        # nothing else in the system will report it.
        "silently_failing": [s.name for s in sources if s.status == SILENT],
        "sources": [s.as_dict() for s in sources],
    }
