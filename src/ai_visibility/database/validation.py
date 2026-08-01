from __future__ import annotations

from typing import Any

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect

EXPECTED_COLUMNS = {
    "id",
    "user_id",
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
JSON_COMPATIBLE_TYPES = {"json", "jsonb", "text", "character varying"}


def check_database(settings: Settings) -> dict[str, Any]:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute("select current_database() as database_name, current_user as database_user")
        identity = cursor.fetchone()
        cursor.execute(
            """
            select column_name, data_type, udt_name
            from information_schema.columns
            where table_schema = 'public' and table_name = 'ai_monitoring'
            order by ordinal_position
            """
        )
        rows = cursor.fetchall()
        if not rows:
            raise RuntimeError("Required source table public.ai_monitoring does not exist.")
        types = {row["column_name"]: row["data_type"] for row in rows}
        missing = sorted(EXPECTED_COLUMNS - types.keys())
        if missing:
            raise RuntimeError(
                "public.ai_monitoring is missing expected columns: " + ", ".join(missing)
            )
        incompatible = sorted(
            name
            for name in {
                "request_payload",
                "answers_list",
                "citations_list",
                "citations_data",
                "companies_data",
            }
            if types[name] not in JSON_COMPATIBLE_TYPES
        )
        if incompatible:
            raise RuntimeError(
                "Source JSON columns have unsupported types: " + ", ".join(incompatible)
            )
        cursor.execute(
            """
            select greatest(coalesce(reltuples, 0), 0)::bigint as row_count
            from pg_class
            where oid = 'public.ai_monitoring'::regclass
            """
        )
        count = cursor.fetchone()
        cursor.execute(
            """
            select exists (
              select 1 from information_schema.tables
              where table_schema = 'public'
                and table_name = 'ai_visibility_schema_migrations'
            ) as normalized_schema_present
            """
        )
        schema = cursor.fetchone()
    return {
        **dict(identity or {}),
        "source_table": "public.ai_monitoring",
        "source_estimated_row_count": (count or {}).get("row_count", 0),
        "source_columns": types,
        "normalized_schema_present": (schema or {}).get("normalized_schema_present", False),
        "source_access": "read_only",
    }
