# executions.py — read the action-execution records that anchor outcome measurement.
# Purpose: answer "was this recommendation actually implemented, and when?" Without that, an
#          outcome can only say growth happened after a REPORT, not after an ACTION.
# Scope: read-only over public.aivc_action_executions (written by the aivc CLI, migrations/0008).
# Consumers: scout/db/outcome_measure.py.
import logging

from scout.db import sed_mapping as m

log = logging.getLogger(__name__)


def load_executions(sb, recommendation_ids: list[str]) -> dict[str, dict]:
    """Return {recommendation_id: execution_row} for the ids that have one.

    A missing id is not an error — it means nobody has confirmed the recommendation shipped, and
    the caller must treat it as awaiting execution rather than measuring it anyway."""
    out: dict[str, dict] = {}
    ids = [str(i) for i in recommendation_ids if i]
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        try:
            resp = (
                sb.table(m.AIVC_ACTION_EXECUTIONS_TABLE)
                .select(",".join(m.AIVC_EXECUTION_READ_COLS))
                .eq("subject_type", "recon_recommendation")
                .in_("subject_id", chunk)
                .execute()
            )
            for row in (resp.data or []):
                out[str(row.get("subject_id"))] = row
        except Exception as e:
            # Fail closed: with no execution data, callers measure nothing rather than falling
            # back to the report date and crediting an action that may never have happened.
            log.warning("[executions] fetch failed for %d ids: %s", len(chunk), e)
    return out


def is_measurable(execution: dict | None) -> bool:
    """True only when the record says the change shipped AND carries the timestamp it shipped on."""
    if not execution:
        return False
    return (
        str(execution.get("status") or "") in m.EXECUTED_STATUSES
        and bool(execution.get("implemented_at"))
    )
