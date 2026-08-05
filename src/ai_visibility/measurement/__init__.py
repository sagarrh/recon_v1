"""Post-execution outcome measurement over the mirrored GSC and GA4 tables.

This package answers one question: after a recommendation was implemented, did the search and
website metrics for the exact pages and queries it targeted actually move?

Three rules shape everything here:

1. **Anchored on execution, never on the report.** Windows are counted from a confirmed
   ``implemented_at``. Measuring from a report date would report growth that merely followed a
   report and present it as the effect of an action.
2. **Missing is not zero.** An unavailable source, an unmapped target and a genuine zero are three
   different results and are never collapsed into one.
3. **Fail closed on tenant ambiguity.** GSC is keyed by ``site_url`` and GA4 by ``property_id``,
   neither of which is a client. Where a handle is shared, measurement is refused rather than
   guessed.
"""

from ai_visibility.measurement.aggregation import (
    GscTotals,
    aggregate_gsc_rows,
    delta,
)
from ai_visibility.measurement.tenancy import TenantScope, resolve_tenant_scope
from ai_visibility.measurement.windows import MeasurementWindow, build_window

__all__ = [
    "GscTotals",
    "MeasurementWindow",
    "TenantScope",
    "aggregate_gsc_rows",
    "build_window",
    "delta",
    "resolve_tenant_scope",
]
