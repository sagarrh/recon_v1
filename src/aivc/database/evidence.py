from __future__ import annotations

from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from aivc.contracts.models import SignalBundle


def load_signal_bundles_for_parent(
    settings: Settings, parent_run_id: UUID
) -> dict[str, SignalBundle]:
    """Load the exact persisted Citation and Recon bundles for a parent run."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(
            """
            select producer, payload from public.aivc_signal_bundles
            where parent_run_id = %s
              and producer in ('ai_visibility', 'scout')
            order by created_at
            """,
            (parent_run_id,),
        ).fetchall()
    bundles: dict[str, SignalBundle] = {}
    for row in rows:
        bundle = SignalBundle.model_validate(row["payload"])
        bundle.verify_checksum()
        bundles[str(row["producer"])] = bundle
    return bundles


def resolve_latest_evidence_parent(
    settings: Settings,
    *,
    client_id: UUID | None = None,
    company_name: str | None = None,
) -> UUID:
    """Resolve the newest parent containing both persisted source bundles."""
    if (client_id is None) == (company_name is None):
        raise ValueError("Provide exactly one client identity for evidence resolution.")
    condition = (
        "pr.client_id = %s" if client_id is not None else "lower(pr.canonical_name) = lower(%s)"
    )
    identity: object = client_id if client_id is not None else str(company_name).strip()
    query = f"""
        select pr.id, pr.client_id, pr.canonical_name
        from public.aivc_pipeline_runs pr
        where {condition}
          and pr.status in ('completed', 'partial')
          and (
            select count(distinct bundle.producer)
            from public.aivc_signal_bundles bundle
            where bundle.parent_run_id = pr.id
              and bundle.producer in ('ai_visibility', 'scout')
          ) = 2
        order by coalesce(pr.completed_at, pr.created_at) desc
        limit 20
    """
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        rows = cursor.execute(query, (identity,)).fetchall()
    if not rows:
        label = client_id or company_name
        raise LookupError(f"No complete persisted evidence parent was found for {label}.")
    identities = {str(row["client_id"]) for row in rows}
    if company_name is not None and len(identities) > 1:
        raise LookupError(
            f"Company '{company_name}' is ambiguous across {len(identities)} clients; "
            "use --client-id or --parent-run-id."
        )
    return UUID(str(rows[0]["id"]))
