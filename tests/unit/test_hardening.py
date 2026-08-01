from __future__ import annotations

from pathlib import Path


def test_rls_migration_covers_tenant_tables() -> None:
    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations" / "0002_tenant_rls.sql").read_text(encoding="utf-8").casefold()
    required_tables = {
        "ai_visibility_companies",
        "ai_visibility_client_companies",
        "ai_visibility_monitor_queries",
        "ai_visibility_run_processing",
        "ai_visibility_answers",
        "ai_visibility_citation_pages",
        "ai_visibility_page_snapshots",
        "ai_visibility_signals",
        "ai_visibility_reports",
    }
    for table in required_tables:
        assert f"alter table public.{table} enable row level security" in sql
    assert "create policy" in sql
    assert "app.client_id" in sql


def test_migrations_never_mutate_raw_source() -> None:
    root = Path(__file__).resolve().parents[2]
    migration_sql = "\n".join(
        path.read_text(encoding="utf-8").casefold()
        for path in sorted((root / "migrations").glob("*.sql"))
    )
    for operation in ("alter", "drop", "truncate", "delete from", "update"):
        assert f"{operation} public.ai_monitoring" not in migration_sql
