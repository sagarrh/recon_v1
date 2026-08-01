from __future__ import annotations

import re
from collections.abc import Iterable

from aivc.contracts.models import stable_id
from aivc.reporting.models import RecommendedAction


def _intent(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _category(value: str) -> str:
    normalized = value.casefold()
    if any(token in normalized for token in ("schema", "structured", "robots", "llms")):
        return "technical_discoverability"
    if any(token in normalized for token in ("review", "third-party", "directory", "press")):
        return "external_authority"
    if any(token in normalized for token in ("monitor", "measure", "track", "retest")):
        return "measurement"
    return "content_authority"


def consolidate_actions(
    candidates: Iterable[tuple[str, str, str | None, str | None]],
    *,
    limit: int,
) -> list[RecommendedAction]:
    """Deduplicate actions by normalized intent while retaining source IDs."""
    selected: dict[str, RecommendedAction] = {}
    for action, source_id, time_horizon, measurement_signal in candidates:
        clean = " ".join(str(action).split())
        if not clean:
            continue
        key = _intent(clean)
        existing = selected.get(key)
        if existing is not None:
            if source_id not in existing.source_ids:
                existing.source_ids.append(source_id)
            continue
        selected[key] = RecommendedAction(
            action_id=stable_id("report-action", key),
            action=clean,
            category=_category(clean),
            time_horizon=time_horizon,
            measurement_signal=(
                measurement_signal
                or "Recheck the related SOV and literal answer visibility in the next cycle."
            ),
            source_ids=[source_id],
        )
        if len(selected) >= limit:
            break
    return list(selected.values())
