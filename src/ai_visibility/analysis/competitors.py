from __future__ import annotations

from typing import Any

from ai_visibility.normalization.models import NormalizedRun
from ai_visibility.utils.text import normalize_name


def competitor_deltas(
    previous: NormalizedRun, current: NormalizedRun, client_company: str
) -> list[dict[str, Any]]:
    client = normalize_name(client_company)
    result: list[dict[str, Any]] = []
    keys = previous.company_metrics.keys() | current.company_metrics.keys()
    for key in keys:
        if key == client:
            continue
        before = previous.company_metrics.get(key)
        after = current.company_metrics.get(key)
        previous_literal_count = before.literal_answer_count if before else 0
        current_literal_count = after.literal_answer_count if after else 0
        previous_literal_visibility = before.literal_visibility if before else 0.0
        current_literal_visibility = after.literal_visibility if after else 0.0
        use_upstream = bool(
            before
            and after
            and before.upstream_count is not None
            and after.upstream_count is not None
            and before.upstream_visibility is not None
            and after.upstream_visibility is not None
        )
        previous_count = (
            int(before.upstream_count)
            if use_upstream and before and before.upstream_count is not None
            else previous_literal_count
        )
        current_count = (
            int(after.upstream_count)
            if use_upstream and after and after.upstream_count is not None
            else current_literal_count
        )
        previous_visibility = (
            float(before.upstream_visibility)
            if use_upstream and before and before.upstream_visibility is not None
            else previous_literal_visibility
        )
        current_visibility = (
            float(after.upstream_visibility)
            if use_upstream and after and after.upstream_visibility is not None
            else current_literal_visibility
        )
        literal_changed = (
            current_literal_count != previous_literal_count
            or abs(current_literal_visibility - previous_literal_visibility) >= 1e-9
        )
        upstream_changed = (
            current_count != previous_count or abs(current_visibility - previous_visibility) >= 1e-9
        )
        if not literal_changed and not upstream_changed:
            continue
        status = (
            "new"
            if previous_count == 0 and current_count > 0
            else "removed"
            if previous_count > 0 and current_count == 0
            else "increased"
            if current_visibility > previous_visibility
            else "decreased"
        )
        metric = after or before
        literal_status = (
            "new"
            if previous_literal_count == 0 and current_literal_count > 0
            else "removed"
            if previous_literal_count > 0 and current_literal_count == 0
            else "increased"
            if current_literal_visibility > previous_literal_visibility
            else "decreased"
            if current_literal_visibility < previous_literal_visibility
            else "stable"
        )
        result.append(
            {
                "company": metric.company_name if metric else key,
                "status": status,
                "previous_mentions": previous_count,
                "current_mentions": current_count,
                "previous_visibility": previous_visibility,
                "current_visibility": current_visibility,
                "visibility_delta": current_visibility - previous_visibility,
                "metric_basis": ("upstream_preserved" if use_upstream else "literal_recomputed"),
                "previous_literal_mentions": previous_literal_count,
                "current_literal_mentions": current_literal_count,
                "previous_literal_visibility": previous_literal_visibility,
                "current_literal_visibility": current_literal_visibility,
                "literal_visibility_delta": (
                    current_literal_visibility - previous_literal_visibility
                ),
                "literal_status": literal_status,
                "upstream_count_difference_from_literal": (
                    current_count - current_literal_count if use_upstream else None
                ),
            }
        )
    return sorted(result, key=lambda item: (-abs(item["visibility_delta"]), item["company"]))
