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
    "cycle_runs": frozenset({"run_id", "sync_date", "mode", "status", "started_at"}),
    "sov_tracking": frozenset(
        {"run_id", "client_id", "cluster_id", "competitor_name", "current_sov"}
    ),
    "investigation_triggers": frozenset(
        {"run_id", "client_id", "trigger_key", "cluster_id", "competitor_name"}
    ),
    "investigations": frozenset(
        {"run_id", "trigger_key", "client_id", "cluster_id", "ai_citation_changes"}
    ),
    # revenue_category is required: without sql/0200_recon_revenue_categories.sql applied to the
    # Supabase project, the recommendation writer fails on an unknown column mid-run. `db audit`
    # is where that must surface, not a live pipeline.
    "recommendations": frozenset(
        {"run_id", "client_id", "cluster_id", "validation_status", "revenue_category"}
    ),
    "reports": frozenset(
        {"run_id", "recommendation_id", "client_id", "validation_status"}
    ),
    "blog_detections": frozenset({"run_id", "client_id", "url"}),
    "scout_decision_log": frozenset(
        {"run_id", "client_id", "cluster_id", "noise", "field"}
    ),
    "scout_outcomes": frozenset(
        {"recommendation_id", "run_id", "client_id", "revenue_category", "target_pages"}
    ),
    "prompt_log": frozenset({"run_id", "node", "model", "total_tokens"}),
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
        "prompt_log",
    }
)

RECON_UPSERT_TARGETS: dict[str, frozenset[str]] = {
    "cycle_runs": frozenset({"run_id"}),
    "sov_tracking": frozenset({"run_id", "client_id", "cluster_id", "competitor_name"}),
    "investigation_triggers": frozenset({"run_id", "trigger_key"}),
    "investigations": frozenset({"run_id", "trigger_key"}),
    "recommendations": frozenset({"run_id", "client_id", "cluster_id"}),
    "reports": frozenset({"run_id", "recommendation_id"}),
    "blog_detections": frozenset({"run_id", "url"}),
    "scout_decision_log": frozenset({"run_id", "client_id", "cluster_id"}),
    "scout_outcomes": frozenset({"recommendation_id"}),
}


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


def assess_unique_targets(
    discovered: Mapping[str, set[frozenset[str]]],
) -> dict[str, dict[str, Any]]:
    """Verify every PostgREST upsert target has a matching unique index."""
    return {
        table: {
            "required_columns": sorted(columns),
            "present": columns in discovered.get(table, set()),
        }
        for table, columns in RECON_UPSERT_TARGETS.items()
    }


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

        unique_rows = connection.execute(
            """
            SELECT table_name, index_name, array_agg(column_name ORDER BY position) AS columns
            FROM (
              SELECT table_rel.relname AS table_name,
                     index_rel.relname AS index_name,
                     attribute.attname AS column_name,
                     key.position
              FROM pg_index index_meta
              JOIN pg_class table_rel ON table_rel.oid = index_meta.indrelid
              JOIN pg_class index_rel ON index_rel.oid = index_meta.indexrelid
              JOIN pg_namespace namespace ON namespace.oid = table_rel.relnamespace
              CROSS JOIN LATERAL unnest(index_meta.indkey)
                WITH ORDINALITY AS key(attribute_number, position)
              JOIN pg_attribute attribute
                ON attribute.attrelid = table_rel.oid
               AND attribute.attnum = key.attribute_number
              WHERE namespace.nspname = 'public'
                AND index_meta.indisunique
                AND table_rel.relname = ANY(%s)
            ) indexed
            GROUP BY table_name, index_name
            """,
            (sorted(RECON_UPSERT_TARGETS),),
        ).fetchall()
        unique_targets: dict[str, set[frozenset[str]]] = {}
        for row in unique_rows:
            unique_targets.setdefault(row["table_name"], set()).add(
                frozenset(str(column) for column in row["columns"])
            )

        mode_rows = connection.execute(
            """
            SELECT pg_get_constraintdef(constraint_meta.oid) AS definition
            FROM pg_constraint constraint_meta
            JOIN pg_class table_rel ON table_rel.oid = constraint_meta.conrelid
            JOIN pg_namespace namespace ON namespace.oid = table_rel.relnamespace
            WHERE namespace.nspname = 'public'
              AND table_rel.relname = 'cycle_runs'
              AND constraint_meta.contype = 'c'
            """
        ).fetchall()

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
    unique_assessment = assess_unique_targets(unique_targets)
    missing_recon_tables = sorted(RECON_WRITE_TABLES - discovered.keys())
    missing_unique_targets = sorted(
        table for table, result in unique_assessment.items() if not result["present"]
    )
    cycle_mode_definitions = [str(row["definition"]) for row in mode_rows]
    cycle_live_mode_allowed = any("'live'" in definition for definition in cycle_mode_definitions)
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
        "ready": (
            required_sources_ready
            and not missing_recon_tables
            and not missing_unique_targets
            and cycle_live_mode_allowed
        ),
        "source_of_truth": "public.ai_monitoring",
        "tables": table_assessment,
        "column_types": column_types,
        "recon_write_tables": {
            "required": sorted(RECON_WRITE_TABLES),
            "missing": missing_recon_tables,
            "unique_targets": unique_assessment,
            "missing_unique_targets": missing_unique_targets,
            "cycle_run_mode_constraints": cycle_mode_definitions,
            "cycle_live_mode_allowed": cycle_live_mode_allowed,
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
