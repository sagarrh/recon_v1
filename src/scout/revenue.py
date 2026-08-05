# revenue.py — Evidence-graded revenue categories (pure, deterministic; the ONLY home for this arithmetic).
# Purpose: Classify what kind of revenue evidence exists for a cluster/asset and carry its value with an
#          explicit category, never a single undifferentiated dollar figure.
# Scope: No SOV->dollar extrapolation. A category is earned by observable linkage, not by arithmetic.
# Consumers: scout/nodes/recommendation_gen.py, scout/db/outcome_measure.py, scout/reports/asset_attribution.py.
#
# Categories (never summed across categories — see sum_within_category):
#   recorded             GA4 purchase revenue on mapped, client-owned target landing pages.
#   influenced           Recorded revenue linked to target pages/sessions, without a sole-causation claim.
#   incremental_estimate Before/after movement adjusted by a control or seasonal baseline (Phase 5 only).
#   modeled_scenario     Assumption-driven planning figure. NOT revenue. Internal-only by default.
#   unavailable          Insufficient evidence for a defensible value. The honest default.
import math
from dataclasses import dataclass, field
from enum import StrEnum


class RevenueCategory(StrEnum):
    recorded = "recorded"
    influenced = "influenced"
    incremental_estimate = "incremental_estimate"
    modeled_scenario = "modeled_scenario"
    unavailable = "unavailable"


# Categories that may appear in client-facing output. modeled_scenario is deliberately absent:
# it is a planning assumption, and placing it beside measured revenue makes it read as a forecast.
CLIENT_VISIBLE_CATEGORIES = frozenset({
    RevenueCategory.recorded,
    RevenueCategory.influenced,
    RevenueCategory.incremental_estimate,
})

# How a target page was tied to the revenue rows. Only exact_page earns `recorded`.
LINKAGE_EXACT_PAGE = "exact_page"
LINKAGE_SESSION = "session_linked"
LINKAGE_NONE = "insufficient_linkage"


@dataclass(frozen=True)
class RevenueFinding:
    """One category-tagged revenue observation. `value_usd` is meaningless without `category`."""
    category: RevenueCategory
    value_usd: float | None
    currency: str = "USD"
    basis_reason: str = ""
    limitations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_client_visible(self) -> bool:
        return self.category in CLIENT_VISIBLE_CATEGORIES


def pct_to_fraction(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    f = f / 100.0
    return max(0.0, min(1.0, f))


def clamp_fraction(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return max(0.0, min(1.0, f))


def normalize_currency(amount, from_currency: str, to_currency: str = "USD",
                       rates: dict | None = None) -> float | None:
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return None
    if math.isnan(amt):
        return None
    if (from_currency or "").upper() == (to_currency or "").upper():
        return amt
    if not rates:
        return None
    rate = rates.get((from_currency.upper(), to_currency.upper()))
    if rate is None:
        return None
    try:
        return amt * float(rate)
    except (TypeError, ValueError):
        return None


# ---- category resolution ----

def resolve_revenue_category(*, page_scoped_revenue: float | None, linkage: str,
                             control_adjusted: bool = False,
                             modeled_value: float | None = None) -> RevenueCategory:
    """Grade the strongest revenue evidence available. Evidence, never arithmetic, decides the category.

    A value with no observable page linkage is never `recorded` or `influenced`, regardless of how it was
    computed. A modeled figure can only ever reach `modeled_scenario`."""
    if page_scoped_revenue is not None and linkage in (LINKAGE_EXACT_PAGE, LINKAGE_SESSION):
        if control_adjusted:
            return RevenueCategory.incremental_estimate
        return (RevenueCategory.recorded if linkage == LINKAGE_EXACT_PAGE
                else RevenueCategory.influenced)
    if modeled_value is not None:
        return RevenueCategory.modeled_scenario
    return RevenueCategory.unavailable


def unavailable(reason: str, *limitations: str) -> RevenueFinding:
    """The honest default. Missing revenue is `unavailable`, never 0 and never a modeled substitute."""
    return RevenueFinding(
        category=RevenueCategory.unavailable,
        value_usd=None,
        basis_reason=reason,
        limitations=tuple(limitations),
    )


def group_by_category(findings) -> dict[RevenueCategory, list[RevenueFinding]]:
    out: dict[RevenueCategory, list[RevenueFinding]] = {}
    for f in findings or []:
        out.setdefault(f.category, []).append(f)
    return out


def sum_within_category(findings, category: RevenueCategory) -> float | None:
    """Total the values of ONE category. This is the only summation API in this module, by design —
    there is deliberately no cross-category total, because adding recorded revenue to a modeled
    scenario produces a number that means nothing."""
    values = [f.value_usd for f in (findings or [])
              if f.category == category and f.value_usd is not None]
    return sum(values) if values else None


def client_visible(findings, *, modeled_scenario_visible: bool = False) -> list[RevenueFinding]:
    """Filter to what may be shown to a client. modeled_scenario is excluded unless explicitly enabled."""
    allowed = set(CLIENT_VISIBLE_CATEGORIES)
    if modeled_scenario_visible:
        allowed.add(RevenueCategory.modeled_scenario)
    return [f for f in (findings or []) if f.category in allowed]


# ---- measured revenue ----

def actual_revenue_from_ga4(*, ga4_rows: list[dict], landing_pages: set[str],
                            normalizer=None) -> float | None:
    """Sum GA4 revenue for the given landing pages ONLY.

    `landing_pages` is required and must be non-empty: property-wide GA4 revenue is not this cluster's
    revenue, and returning it here is how unrelated revenue used to acquire a cluster's label.

    `normalizer` must be the SAME function used to normalize `landing_pages`, and is applied to each
    row's landing_page so both sides of the comparison are in one form. GA4 emits either a path or an
    absolute URL depending on the property; comparing the two forms directly matches nothing and
    silently reports `unavailable` for revenue that genuinely exists. Pass
    scout.db.revenue_context.normalize_url unless the caller has already normalized both sides."""
    import logging
    _log = logging.getLogger(__name__)
    if not landing_pages:
        _log.warning("[revenue] GA4 revenue requested with no target landing pages — returning None")
        return None
    normalize = normalizer or (lambda value: value)
    total = 0.0
    count = 0
    skipped = 0
    for row in (ga4_rows or []):
        lp = normalize(row.get("landing_page") or "")
        if lp not in landing_pages:
            continue
        rev = row.get("revenue")
        if rev is None:
            continue
        try:
            total += float(rev)
            count += 1
        except (TypeError, ValueError):
            skipped += 1
            continue
    if skipped > 0:
        _log.warning("[revenue] %d GA4 rows had non-numeric revenue values (skipped)", skipped)
    return total if count > 0 else None


def delta_revenue(*, baseline_revenue: float | None,
                  current_revenue: float | None) -> float | None:
    if baseline_revenue is None or current_revenue is None:
        return None
    try:
        return float(current_revenue) - float(baseline_revenue)
    except (TypeError, ValueError):
        return None


# ---- internal experimental model (NEVER client-facing) ----

def revenue_at_risk(*, client_revenue: float | None, competitor_share: float | None,
                    client_share: float | None) -> float | None:
    """EXPERIMENTAL, INTERNAL ONLY. Share-weighted split of the client's own measured revenue.

    This is a competitive framing device, not a measurement. It must never be published to a client
    and must never be presented as a revenue category. Retained for internal triage comparison only."""
    if any(v is None for v in (client_revenue, competitor_share, client_share)):
        return None
    try:
        cr, cs, cmp = float(client_revenue), float(client_share), float(competitor_share)
    except (TypeError, ValueError):
        return None
    denom = cs + cmp
    if denom <= 0:
        return 0.0
    return max(0.0, cr * cmp / denom)


def modeled_value_scenario(*, impressions: float | None, client_ctr_pct: float | None,
                           conversion_rate_pct: float | None, average_order_value: float | None,
                           client_sov_share: float | None,
                           capture_fraction: float) -> RevenueFinding:
    """A planning scenario, NOT revenue: impressions x CTR x capture x SOV x CR x AOV.

    `capture_fraction` is an explicit assumption with no defensible empirical basis and must be passed
    in deliberately. Every chained coefficient widens the error, so the result can only ever be
    `modeled_scenario` and its assumptions travel with it in `limitations`."""
    ctr = pct_to_fraction(client_ctr_pct)
    cr = pct_to_fraction(conversion_rate_pct)
    cap = clamp_fraction(capture_fraction)
    sov_frac = clamp_fraction((client_sov_share or 0) * 0.01) if client_sov_share is not None else None
    try:
        aov = float(average_order_value)
    except (TypeError, ValueError):
        aov = None
    if any(v is None for v in (impressions, ctr, cr, cap, sov_frac, aov)):
        return unavailable("modeled scenario inputs incomplete")
    try:
        imp = float(impressions)
    except (TypeError, ValueError):
        return unavailable("modeled scenario impressions not numeric")
    if math.isnan(imp):
        return unavailable("modeled scenario impressions not numeric")
    value = imp * ctr * cap * sov_frac * cr * max(aov, 0.0)
    return RevenueFinding(
        category=RevenueCategory.modeled_scenario,
        value_usd=value,
        basis_reason="modeled: impressions x ctr x capture x sov x conversion x aov",
        limitations=(
            f"assumed AI capture fraction {cap}",
            "SOV is not traffic share",
            "traffic share is not conversion share",
            "not measured revenue",
        ),
    )


# ---- cluster-level assembly ----

def _scenario_for(demand: dict, financials: dict, sov: dict,
                  capture_fraction: float | None) -> RevenueFinding | None:
    """Build the internal modeled scenario, or None when it is disabled or its inputs are incomplete."""
    if capture_fraction is None:
        return None
    scenario = modeled_value_scenario(
        impressions=demand.get("impressions"),
        client_ctr_pct=financials.get("estimated_ctr"),
        conversion_rate_pct=financials.get("conversion_rate"),
        average_order_value=financials.get("average_order_value"),
        client_sov_share=sov.get("client_sov_pp"),
        capture_fraction=capture_fraction,
    )
    return scenario if scenario.category is RevenueCategory.modeled_scenario else None


def _graded_value(category: RevenueCategory, *, page_revenue: float | None, pages: set[str],
                  currency: str, fx_rates: dict | None,
                  scenario: RevenueFinding | None) -> tuple[float | None, tuple[str, ...], str]:
    """Resolve (value_usd, limitations, basis_reason) for an already-graded category."""
    if category in (RevenueCategory.recorded, RevenueCategory.influenced):
        converted = normalize_currency(page_revenue, currency, "USD", fx_rates)
        if converted is None:
            return (page_revenue,
                    (f"revenue in {currency} could not be converted to USD",),
                    f"GA4 purchase revenue on {len(pages)} mapped target page(s)")
        return (converted, (), f"GA4 purchase revenue on {len(pages)} mapped target page(s)")
    if category is RevenueCategory.modeled_scenario and scenario is not None:
        return (scenario.value_usd, scenario.limitations, scenario.basis_reason)
    return (None, (), "no measured revenue linkage for this cluster")


def compute_cluster_revenue(*, demand: dict, financials: dict, sov: dict,
                            ga4: dict | list | None, fx_rates: dict | None,
                            target_landing_pages: set[str] | None = None,
                            capture_fraction: float | None = None,
                            normalizer=None) -> dict:
    """Grade the revenue evidence for one cluster. Returns a category, never an 'opportunity'.

    Measured GA4 revenue is scoped to `target_landing_pages`; with no mapped pages there is no linkage
    and the result is `unavailable`, optionally accompanied by an internal modeled scenario.
    `normalizer` must be the same function used to normalize `target_landing_pages` — see
    actual_revenue_from_ga4."""
    currency = financials.get("currency", "USD")
    pages = target_landing_pages or set()

    ga4_rows = ga4 if isinstance(ga4, list) else []
    page_revenue = (actual_revenue_from_ga4(ga4_rows=ga4_rows, landing_pages=pages,
                                            normalizer=normalizer)
                    if pages else None)
    linkage = LINKAGE_EXACT_PAGE if page_revenue is not None else LINKAGE_NONE

    scenario = _scenario_for(demand, financials, sov, capture_fraction)
    category = resolve_revenue_category(
        page_scoped_revenue=page_revenue,
        linkage=linkage,
        modeled_value=scenario.value_usd if scenario else None,
    )
    value_usd, limitations, basis_reason = _graded_value(
        category, page_revenue=page_revenue, pages=pages, currency=currency,
        fx_rates=fx_rates, scenario=scenario,
    )

    # Internal-only competitive framing; never surfaced as a category.
    at_risk = revenue_at_risk(
        client_revenue=value_usd if category is not RevenueCategory.modeled_scenario else None,
        competitor_share=clamp_fraction((sov.get("primary_competitor_sov_pp") or 0) * 0.01),
        client_share=clamp_fraction((sov.get("client_sov_pp") or 0) * 0.01),
    )

    return {
        "revenue_category": str(category),
        "revenue_value_usd": value_usd,
        "revenue_currency": currency,
        "revenue_limitations": list(limitations),
        "revenue_at_risk_usd_internal": at_risk,
        "revenue_inputs": {
            "basis_reason": basis_reason,
            "linkage": linkage,
            "target_page_count": len(pages),
            "ga4_page_revenue": page_revenue,
            "impressions": demand.get("impressions"),
            "client_sov_pp": sov.get("client_sov_pp"),
            "primary_competitor_sov_pp": sov.get("primary_competitor_sov_pp"),
            "currency": currency,
            "fx_rate": (fx_rates or {}).get(((currency or "USD").upper(), "USD")),
        },
    }


# ---- per-asset attribution scoring (pure, deterministic) ----

def coverage_score(attributed, total) -> float | None:
    if attributed is None or total is None:
        return None
    try:
        a, t = float(attributed), float(total)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return max(0.0, min(1.0, a / t))


def confidence_score(*, channel_weight: float, coverage, lag: float,
                     is_modeled: bool, modeled_discount: float) -> float | None:
    if coverage is None:
        return None
    raw = channel_weight * coverage * lag
    if is_modeled:
        raw *= modeled_discount
    return max(0.0, min(1.0, raw))
