"""The execution funnel and the north-star metric.

    proposed -> accepted -> executed -> verified -> measured -> positive

The north star is the share of EXECUTED recommendations that produced a measurable positive
downstream outcome. Deliberately not "revenue attributed by Recon": that number can be made to grow
without anything improving for the client.

Two reporting rules matter more than the headline:

**Null and negative rates are first-class, not residuals.** Recon chooses which recommendations to
make, how long to wait, and what counts as positive. Optimising a lone positive rate converges on
recommending safe things in already-growing topics. Publishing the null and negative rates alongside
it makes that failure visible instead of flattering.

**Drop-off is the finding, not the noise.** If most recommendations are never executed, that is the
product problem, and it matters more early on than whether the executed ones worked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect

# Outcome classifications that count as a measurable positive result.
POSITIVE_CLASSIFICATIONS = frozenset({"associated_search_growth"})
NEGATIVE_CLASSIFICATIONS = frozenset({"associated_search_decline"})
NULL_CLASSIFICATIONS = frozenset({"no_material_change", "mixed_result"})
# Present but not yet a verdict — excluded from every rate rather than counted as failures.
PENDING_CLASSIFICATIONS = frozenset({
    "waiting_for_window", "insufficient_data", "unmapped", "source_not_connected",
})


@dataclass(frozen=True)
class ExecutionFunnel:
    proposed: int
    accepted: int
    executed: int
    verified: int
    measured: int
    positive: int
    null_result: int
    negative: int
    pending: int

    def _rate(self, numerator: int, denominator: int) -> float | None:
        """None, not zero, when there is no denominator — an undefined rate is not a rate of 0%."""
        return (numerator / denominator) if denominator else None

    @property
    def execution_rate(self) -> float | None:
        return self._rate(self.executed, self.proposed)

    @property
    def measurement_rate(self) -> float | None:
        return self._rate(self.measured, self.executed)

    @property
    def north_star(self) -> float | None:
        """Share of EXECUTED recommendations with a measurable positive downstream outcome."""
        return self._rate(self.positive, self.executed)

    @property
    def positive_rate_of_measured(self) -> float | None:
        """Share of MEASURED recommendations that were positive. Reported beside the north star
        because the two diverge exactly when measurement coverage is poor."""
        return self._rate(self.positive, self.measured)

    def as_dict(self) -> dict[str, Any]:
        return {
            "funnel": {
                "proposed": self.proposed,
                "accepted": self.accepted,
                "executed": self.executed,
                "verified": self.verified,
                "measured": self.measured,
            },
            "results": {
                "positive": self.positive,
                "no_material_change_or_mixed": self.null_result,
                "negative": self.negative,
                "pending_or_unmeasurable": self.pending,
            },
            "rates": {
                "north_star_positive_of_executed": self.north_star,
                "positive_of_measured": self.positive_rate_of_measured,
                "execution_rate": self.execution_rate,
                "measurement_rate_of_executed": self.measurement_rate,
            },
            "notes": [
                "north star = measurable positive downstream outcome / executed recommendations",
                "null and negative rates are published alongside the positive rate on purpose: "
                "a positive rate alone can be raised by recommending only safe actions",
                "pending items are excluded from every rate rather than counted as failures",
            ],
        }


_FUNNEL_SQL = """
    select
      (select count(*) from public.recommendations
        where client_id = %(client)s)                                          as proposed,
      (select count(*) from public.aivc_action_executions
        where client_id = %(client)s and status = 'accepted')                  as accepted,
      (select count(*) from public.aivc_action_executions
        where client_id = %(client)s and status in ('executed','verified'))    as executed,
      (select count(*) from public.aivc_action_executions
        where client_id = %(client)s and status = 'verified')                  as verified
"""

_CLASSIFICATION_SQL = """
    select measured.classification, count(*) as n
      from public.ai_visibility_measurement_outcomes measured
      join public.ai_visibility_measurement_plans plan on plan.id = measured.plan_id
     where plan.client_id = %s
     group by 1
"""


def load_execution_funnel(settings: Settings, *, client_id: UUID) -> ExecutionFunnel:
    """Count the funnel for one client from live execution and measurement state."""
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        counts = cursor.execute(_FUNNEL_SQL, {"client": client_id}).fetchone() or {}
        classifications = {
            str(row["classification"]): int(row["n"])
            for row in cursor.execute(_CLASSIFICATION_SQL, (client_id,)).fetchall()
        }

    positive = sum(n for c, n in classifications.items() if c in POSITIVE_CLASSIFICATIONS)
    negative = sum(n for c, n in classifications.items() if c in NEGATIVE_CLASSIFICATIONS)
    null_result = sum(n for c, n in classifications.items() if c in NULL_CLASSIFICATIONS)
    pending = sum(n for c, n in classifications.items() if c in PENDING_CLASSIFICATIONS)

    return ExecutionFunnel(
        proposed=int(counts.get("proposed") or 0),
        accepted=int(counts.get("accepted") or 0),
        executed=int(counts.get("executed") or 0),
        verified=int(counts.get("verified") or 0),
        # "Measured" means a verdict was reached, not merely that a row exists.
        measured=positive + negative + null_result,
        positive=positive,
        null_result=null_result,
        negative=negative,
        pending=pending,
    )
