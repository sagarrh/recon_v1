from __future__ import annotations

from pathlib import Path

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.utils.hashing import sha256_text

_FORBIDDEN_SOURCE_MUTATIONS = (
    "alter table public.ai_monitoring",
    "drop table public.ai_monitoring",
    "truncate public.ai_monitoring",
    "delete from public.ai_monitoring",
    "update public.ai_monitoring",
    "insert into public.ai_monitoring",
)


def migration_directory() -> Path:
    return Path(__file__).resolve().parents[3] / "migrations"


def apply_migrations(settings: Settings) -> list[str]:
    applied: list[str] = []
    files = sorted(migration_directory().glob("*.sql"))
    with connect(settings) as connection:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            lowered = " ".join(sql.casefold().split())
            forbidden = [token for token in _FORBIDDEN_SOURCE_MUTATIONS if token in lowered]
            if forbidden:
                raise RuntimeError(f"Unsafe source-table mutation in {path.name}: {forbidden[0]}")
            checksum = sha256_text(sql)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select to_regclass('public.ai_visibility_schema_migrations') as relation
                    """
                )
                exists = (cursor.fetchone() or {}).get("relation") is not None
                if exists:
                    cursor.execute(
                        """
                        select checksum from public.ai_visibility_schema_migrations
                        where version = %s
                        """,
                        (path.name,),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        if existing["checksum"] != checksum:
                            raise RuntimeError(
                                f"Applied migration {path.name} has a different checksum."
                            )
                        continue
                cursor.execute(sql)
                cursor.execute(
                    """
                    insert into public.ai_visibility_schema_migrations(version, checksum)
                    values (%s, %s)
                    on conflict (version) do nothing
                    """,
                    (path.name, checksum),
                )
            connection.commit()
            applied.append(path.name)
    return applied
