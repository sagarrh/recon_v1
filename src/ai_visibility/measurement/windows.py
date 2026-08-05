"""Equal baseline and follow-up windows, anchored on a confirmed implementation date.

Two rules do the work here:

**The anchor is the implementation, not the report.** A window counted from when a recommendation
was written measures growth that followed a report. Only a window counted from when the change
actually shipped can speak about the effect of an action.

**Windows are equal length and complete.** An unequal comparison flatters whichever side is longer,
and a partial follow-up window reads as a decline that has not happened yet. Both Google
integrations clamp their data to today-2, so a follow-up window is not evaluable until its last day
is at least that old.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

READY = "ready"                          # both windows complete and inside available data
WAITING_FOR_WINDOW = "waiting_for_window"  # follow-up has not finished, or is not settled yet
NO_ANCHOR = "no_anchor"                  # nothing was confirmed implemented; nothing to measure


@dataclass(frozen=True)
class MeasurementWindow:
    """A baseline/follow-up pair of equal length, plus whether it may be evaluated yet."""

    anchor: date | None
    baseline_start: date | None
    baseline_end: date | None
    follow_up_start: date | None
    follow_up_end: date | None
    window_days: int
    status: str
    reason: str
    # The last date the source can be trusted to have complete data for.
    fresh_through: date | None = None

    @property
    def ready(self) -> bool:
        return self.status == READY

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchor": self.anchor,
            "baseline_start": self.baseline_start,
            "baseline_end": self.baseline_end,
            "follow_up_start": self.follow_up_start,
            "follow_up_end": self.follow_up_end,
            "window_days": self.window_days,
            "status": self.status,
            "reason": self.reason,
            "fresh_through": self.fresh_through,
        }


def build_window(
    implemented_on: date | None,
    *,
    today: date,
    window_days: int = 28,
    source_lag_days: int = 2,
) -> MeasurementWindow:
    """Build the baseline/follow-up pair for one implementation date.

    Baseline is the `window_days` complete days ending the day BEFORE the anchor; follow-up is the
    `window_days` complete days starting the day AFTER. The anchor day itself belongs to neither:
    a change shipped mid-day contaminates both sides of its own comparison.
    """
    fresh_through = today - timedelta(days=source_lag_days)

    if implemented_on is None:
        return MeasurementWindow(
            anchor=None, baseline_start=None, baseline_end=None,
            follow_up_start=None, follow_up_end=None, window_days=window_days,
            status=NO_ANCHOR,
            reason="no confirmed implementation date; measuring from the report date would "
                   "attribute growth to an action that may never have shipped",
            fresh_through=fresh_through,
        )

    baseline_end = implemented_on - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=window_days - 1)
    follow_up_start = implemented_on + timedelta(days=1)
    follow_up_end = follow_up_start + timedelta(days=window_days - 1)

    if follow_up_end > fresh_through:
        days_remaining = (follow_up_end - fresh_through).days
        return MeasurementWindow(
            anchor=implemented_on, baseline_start=baseline_start, baseline_end=baseline_end,
            follow_up_start=follow_up_start, follow_up_end=follow_up_end,
            window_days=window_days, status=WAITING_FOR_WINDOW,
            reason=(
                f"follow-up window ends {follow_up_end} but the source is only complete through "
                f"{fresh_through}; {days_remaining} more day(s) needed"
            ),
            fresh_through=fresh_through,
        )

    return MeasurementWindow(
        anchor=implemented_on, baseline_start=baseline_start, baseline_end=baseline_end,
        follow_up_start=follow_up_start, follow_up_end=follow_up_end,
        window_days=window_days, status=READY,
        reason=f"equal {window_days}-day windows either side of {implemented_on}",
        fresh_through=fresh_through,
    )
