from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect

TABLE_REQUIREMENTS: dict[str, frozenset[str]] = {
    "clients": frozenset(
        {
            "client_id",
            "client_name",
            "company_domain",
            "company_website",
            "competitors",
            "competitors_url",
        }
    ),
    "onboarding": frozenset({"client_id", "company_name", "company_domain"}),
    "ai_monitoring": frozenset(
        {
            "id",
            "client_id",
            "cluster_id",
            "cluster_name",
            "request_payload",
            "answers_list",
            "citations_list",
            "citations_data",
            "companies_data",
            "created_at",
        }
    ),
    "ai_responses": frozenset(
        {
            "id",
            "client_id",
            "cluster_id",
            "cluster_name",
            "week_date",
            "platform",
            "query",
            "answers_list",
            "citations_list",
            "citations_data",
            "synced_at",
        }
    ),
    "sov_weekly": frozenset({"client_id", "cluster_id", "week_date", "top_companies", "synced_at"}),
}

RECON_WRITE_TABLES = frozenset(
    {
        "cycle_runs",
        "sov_tracking",
        "investigation_triggers",
        "investigations",
        "recommendations",
        "reports",
        "blog_detections",
        "scout_decision_log",
        "scout_outcomes",
    }
)


def assess_columns(
    discovered: Mapping[str, set[str]],
) -> dict[str, dict[str, Any]]:
    """Compare catalog results with the minimum integration contract."""
    assessment: dict[str, dict[str, Any]] = {}
    for table, required in TABLE_REQUIREMENTS.items():
        actual = discovered.get(table, set())
        missing = sorted(required - actual)
        assessment[table] = {
            "present": table in discovered,
            "required_columns_present": not missing,
            "missing_columns": missing,
            "column_count": len(actual),
        }
    return assessment


def audit_shared_schema(settings: Settings) -> dict[str, Any]:
    """Audit shared source/Recon structures without reading secrets or writing data."""
    inspected = sorted(set(TABLE_REQUIREMENTS) | RECON_WRITE_TABLES)
    with connect(settings) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        column_rows = connection.execute(
            """
            SELECT table_name, column_name, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            ORDER BY table_name, ordinal_position
            """,
            (inspected,),
        ).fetchall()
        discovered: dict[str, set[str]] = {}
        column_types: dict[str, dict[str, str]] = {}
        for row in column_rows:
            table = row["table_name"]
            discovered.setdefault(table, set()).add(row["column_name"])
            column_types.setdefault(table, {})[row["column_name"]] = row["udt_name"]

        stats = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM public.clients) AS client_count,
              (SELECT count(*) FROM public.clients
                 WHERE nullif(btrim(company_domain), '') IS NOT NULL) AS clients_with_domain,
              (SELECT count(*) FROM (
                 SELECT lower(btrim(client_name))
                 FROM public.clients
                 GROUP BY lower(btrim(client_name)) HAVING count(*) > 1
               ) duplicate_names) AS duplicate_client_names,
              (SELECT count(*) FROM public.ai_monitoring) AS source_run_count,
              (SELECT max(created_at) FROM public.ai_monitoring) AS source_latest_at,
              (SELECT count(*) FROM public.ai_responses) AS mirror_run_count,
              (SELECT max(synced_at) FROM public.ai_responses) AS mirror_latest_at,
              (SELECT count(*) FROM public.clients c JOIN public.onboarding o
                 ON o.client_id = c.client_id) AS client_onboarding_matches,
              (SELECT count(*) FROM public.clients c
                 WHERE EXISTS (
                   SELECT 1 FROM public.ai_monitoring a WHERE a.client_id = c.client_id
                 )) AS clients_with_source_runs
            """
        ).fetchone()
        if stats is None:
            raise RuntimeError("Database schema audit returned no aggregate row.")

    table_assessment = assess_columns(discovered)
    missing_recon_tables = sorted(RECON_WRITE_TABLES - discovered.keys())
    source_latest = stats["source_latest_at"]
    mirror_latest = stats["mirror_latest_at"]
    mirror_lag_seconds = None
    if source_latest is not None and mirror_latest is not None:
        mirror_lag_seconds = (source_latest - mirror_latest).total_seconds()

    required_sources_ready = all(
        item["present"] and item["required_columns_present"] for item in table_assessment.values()
    )
    return {
        "mode": "read_only",
        "ready": required_sources_ready and not missing_recon_tables,
        "source_of_truth": "public.ai_monitoring",
        "tables": table_assessment,
        "column_types": column_types,
        "recon_write_tables": {
            "required": sorted(RECON_WRITE_TABLES),
            "missing": missing_recon_tables,
        },
        "identity": {
            "client_count": stats["client_count"],
            "duplicate_normalized_client_names": stats["duplicate_client_names"],
            "client_onboarding_id_matches": stats["client_onboarding_matches"],
            "clients_with_source_runs": stats["clients_with_source_runs"],
            "clients_with_domain": stats["clients_with_domain"],
        },
        "freshness": {
            "source_run_count": stats["source_run_count"],
            "source_latest_at": source_latest,
            "mirror_run_count": stats["mirror_run_count"],
            "mirror_latest_at": mirror_latest,
            "mirror_lag_seconds": mirror_lag_seconds,
            "mirror_is_complete_source": False,
            "reason": (
                "ai_responses omits request_payload and companies_data; full citation analysis "
                "must read immutable public.ai_monitoring directly"
            ),
        },
    }
