from __future__ import annotations

from datetime import date
from importlib.resources import files
from typing import Any
from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect

_FORBIDDEN = (
    " insert ",
    " update ",
    " delete ",
    " alter ",
    " drop ",
    " truncate ",
    " create ",
    " grant ",
    " revoke ",
)


def recon_report_sql() -> str:
    sql = (
        files("aivc.resources.sql")
        .joinpath("recon_report.sql")
        .read_text(encoding="utf-8")
    )
    flattened = f" {' '.join(sql.casefold().split())} "
    forbidden = next((token.strip() for token in _FORBIDDEN if token in flattened), None)
    if forbidden is not None:
        raise RuntimeError(f"Recon reporting SQL contains forbidden statement: {forbidden}")
    if not flattened.lstrip().startswith("/*") or " with params as " not in flattened:
        raise RuntimeError("Recon reporting SQL does not match the expected read-only query.")
    return sql


def load_recon_reporting_payload(
    settings: Settings,
    *,
    client_id: UUID,
    report_week: date | None,
    history_weeks: int,
) -> dict[str, Any]:
    """Execute the complete frontend-aligned Recon query in a read-only transaction."""
    if history_weeks < 1 or history_weeks > 104:
        raise ValueError("history_weeks must be between 1 and 104")
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        row = cursor.execute(
            recon_report_sql(),
            (client_id, report_week, history_weeks),
        ).fetchone()
    if row is None or not isinstance(row.get("client_facing_recon_report"), dict):
        raise RuntimeError("Recon reporting query returned no client payload.")
    payload = dict(row["client_facing_recon_report"])
    returned_id = str(payload.get("client", {}).get("client_id", ""))
    if returned_id != str(client_id):
        raise RuntimeError("Recon reporting query returned a different client identity.")
    return payload
