"""Deterministic aggregation of GSC rows, and deltas between two windows.

Two aggregation rules are easy to get wrong and wrong in a way that looks plausible:

**CTR is recomputed from totals**, never averaged across rows. Averaging row-level CTR gives every
row equal say regardless of volume, so one obscure query with a 100% CTR on two impressions can
outweigh a page with thousands.

**Average position is weighted by impressions**, never averaged flat. An unweighted mean lets a
query nobody sees move the headline number as much as the one that matters.

Both mistakes produce numbers that move convincingly in the wrong direction, which is worse than
producing none.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# A metric with no data is not a metric with value zero.
UNIT_COUNT = "count"
UNIT_PERCENT = "percentage"
UNIT_POSITION = "position"

# Smaller is better for average position, so its direction is read inverted.
LOWER_IS_BETTER = frozenset({"average_position"})


@dataclass(frozen=True)
class GscTotals:
    """Aggregate GSC performance over one window."""

    impressions: int
    clicks: int
    ctr_percent: float | None
    average_position: float | None
    distinct_queries: int
    distinct_pages: int
    row_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "impressions": self.impressions,
            "clicks": self.clicks,
            "ctr_percent": self.ctr_percent,
            "average_position": self.average_position,
            "distinct_queries": self.distinct_queries,
            "distinct_pages": self.distinct_pages,
            "row_count": self.row_count,
        }


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # NaN check without importing math


def aggregate_gsc_rows(rows: Iterable[dict[str, Any]]) -> GscTotals:
    """Total a set of GSC query/page rows.

    Rows missing a position contribute their impressions and clicks but are excluded from the
    weighted position, so a partial column never silently drags the average toward zero — which
    would read as a top-of-page ranking."""
    impressions = 0
    clicks = 0
    weighted_position = 0.0
    positioned_impressions = 0
    queries: set[str] = set()
    pages: set[str] = set()
    row_count = 0

    for row in rows or []:
        row_count += 1
        row_impressions = _number(row.get("impressions")) or 0.0
        row_clicks = _number(row.get("clicks")) or 0.0
        impressions += int(row_impressions)
        clicks += int(row_clicks)

        position = _number(row.get("position"))
        if position is not None and row_impressions > 0:
            weighted_position += position * row_impressions
            positioned_impressions += int(row_impressions)

        if row.get("query"):
            queries.add(str(row["query"]))
        if row.get("page"):
            pages.add(str(row["page"]))

    return GscTotals(
        impressions=impressions,
        clicks=clicks,
        # Recomputed from totals — never the mean of row-level CTRs.
        ctr_percent=(100.0 * clicks / impressions) if impressions > 0 else None,
        # Impression-weighted — never a flat mean of row positions.
        average_position=(
            weighted_position / positioned_impressions if positioned_impressions > 0 else None
        ),
        distinct_queries=len(queries),
        distinct_pages=len(pages),
        row_count=row_count,
    )


def delta(name: str, baseline: object, follow_up: object, *, unit: str) -> dict[str, Any]:
    """Compare one metric across two windows, preserving what could not be computed.

    A zero baseline yields an absolute delta and a null relative delta: the growth is real but its
    ratio is undefined, and reporting an infinite or capped percentage would be an invention.
    """
    base = _number(baseline)
    current = _number(follow_up)
    if base is None or current is None:
        return {
            "metric": name,
            "unit": unit,
            "baseline": base,
            "follow_up": current,
            "absolute_delta": None,
            "relative_delta": None,
            "direction": "unavailable",
            "note": "not measurable in one or both windows",
        }

    absolute = current - base
    relative = (absolute / base) if base else None
    # Smaller is better for average position, so its direction is read inverted.
    improved = absolute < 0 if name in LOWER_IS_BETTER else absolute > 0
    direction = "improved" if improved else "declined"
    if absolute == 0:
        direction = "flat"

    return {
        "metric": name,
        "unit": unit,
        "baseline": base,
        "follow_up": current,
        "absolute_delta": absolute,
        "relative_delta": relative,
        "direction": direction,
        "note": "new activity from a zero baseline" if base == 0 and absolute != 0 else None,
    }


def compare_windows(baseline: GscTotals, follow_up: GscTotals) -> list[dict[str, Any]]:
    """Full GSC delta set for one measurement plan."""
    return [
        delta("impressions", baseline.impressions, follow_up.impressions, unit=UNIT_COUNT),
        delta("clicks", baseline.clicks, follow_up.clicks, unit=UNIT_COUNT),
        delta("ctr_percent", baseline.ctr_percent, follow_up.ctr_percent, unit=UNIT_PERCENT),
        delta(
            "average_position",
            baseline.average_position,
            follow_up.average_position,
            unit=UNIT_POSITION,
        ),
    ]
