import math

from scout.config import get_config

REVENUE_AI_REFERRAL_CAPTURE_FRACTION = 0.15
_SOV_PP_TO_FRACTION = 0.01


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


def modeled_revenue_from_demand(*, impressions: float | None, client_ctr_pct: float | None,
                                conversion_rate_pct: float | None, average_order_value: float | None,
                                client_sov_share: float | None,
                                capture_fraction: float = REVENUE_AI_REFERRAL_CAPTURE_FRACTION) -> dict | None:
    ctr = pct_to_fraction(client_ctr_pct)
    cr = pct_to_fraction(conversion_rate_pct)
    cap = clamp_fraction(capture_fraction)
    sov_frac = clamp_fraction((client_sov_share or 0) * _SOV_PP_TO_FRACTION) if client_sov_share is not None else None
    try:
        aov = float(average_order_value)
    except (TypeError, ValueError):
        aov = None
    if any(v is None for v in (impressions, ctr, cr, cap, sov_frac, aov)):
        return None
    try:
        imp = float(impressions)
    except (TypeError, ValueError):
        return None
    if math.isnan(imp):
        return None
    expected_clicks = imp * ctr
    ai_reachable_clicks = expected_clicks * cap
    client_clicks = ai_reachable_clicks * sov_frac
    conversions = client_clicks * cr
    revenue = conversions * max(aov, 0.0)
    return {
        "expected_clicks": expected_clicks,
        "ai_reachable_clicks": ai_reachable_clicks,
        "client_clicks": client_clicks,
        "conversions": conversions,
        "revenue": revenue,
        "inputs": {
            "impressions": imp,
            "estimated_ctr_pct": client_ctr_pct,
            "conversion_rate_pct": conversion_rate_pct,
            "average_order_value": aov,
            "client_sov_pp": client_sov_share,
            "capture_fraction": capture_fraction,
        },
    }


def actual_revenue_from_ga4(*, ga4_rows: list[dict],
                            landing_pages: set[str] | None = None) -> float | None:
    import logging
    _log = logging.getLogger(__name__)
    total = 0.0
    count = 0
    skipped = 0
    for row in (ga4_rows or []):
        if landing_pages is not None:
            lp = row.get("landing_page") or ""
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


def revenue_at_risk(*, client_revenue: float | None, competitor_share: float | None,
                    client_share: float | None) -> float | None:
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


def revenue_opportunity(*, addressable_revenue: float | None,
                        client_share: float | None) -> float | None:
    if addressable_revenue is None or client_share is None:
        return None
    try:
        ar, cs = float(addressable_revenue), float(client_share)
    except (TypeError, ValueError):
        return None
    return max(0.0, ar * (1.0 - max(0.0, min(1.0, cs))))


def delta_revenue(*, baseline_revenue: float | None,
                  current_revenue: float | None) -> float | None:
    if baseline_revenue is None or current_revenue is None:
        return None
    try:
        return float(current_revenue) - float(baseline_revenue)
    except (TypeError, ValueError):
        return None


def resolve_basis(*, has_ga4_revenue: bool, has_gsc_demand: bool,
                  has_modeled_inputs: bool) -> str:
    if has_ga4_revenue and has_gsc_demand:
        return "actual"
    if has_ga4_revenue or has_gsc_demand:
        return "hybrid" if has_modeled_inputs else "actual"
    if has_modeled_inputs:
        return "modeled"
    return "none"


def compute_cluster_revenue(*, demand: dict, financials: dict, sov: dict,
                            ga4: dict | None, fx_rates: dict | None) -> dict:
    cfg = get_config()
    capture = cfg.revenue_ai_referral_capture_fraction
    coeff_version = cfg.revenue_coefficient_version

    impressions = demand.get("impressions")
    aov = financials.get("average_order_value")
    cr_pct = financials.get("conversion_rate")
    ctr_pct = financials.get("estimated_ctr")
    currency = financials.get("currency", "USD")
    client_sov_pp = sov.get("client_sov_pp")
    comp_sov_pp = sov.get("primary_competitor_sov_pp")

    has_modeled = all(v is not None and v != 0 for v in (aov, cr_pct, ctr_pct))
    has_ga4 = bool(ga4) and any(r.get("revenue") is not None for r in (ga4 if isinstance(ga4, list) else []))
    has_gsc = impressions is not None

    ga4_rev = None
    if has_ga4 and isinstance(ga4, list):
        ga4_rev = actual_revenue_from_ga4(ga4_rows=ga4)

    modeled = modeled_revenue_from_demand(
        impressions=impressions,
        client_ctr_pct=ctr_pct,
        conversion_rate_pct=cr_pct,
        average_order_value=aov,
        client_sov_share=client_sov_pp,
        capture_fraction=capture,
    )

    client_rev = ga4_rev if ga4_rev is not None else (modeled["revenue"] if modeled else None)

    basis = resolve_basis(has_ga4_revenue=has_ga4, has_gsc_demand=has_gsc, has_modeled_inputs=has_modeled)

    if client_rev is None or client_rev == 0:
        return {
            "revenue_at_risk_usd": None,
            "revenue_opportunity_usd": None,
            "revenue_basis": "none" if client_rev is None else basis,
            "revenue_inputs": {
                "basis_reason": "insufficient inputs" if client_rev is None else "zero revenue",
                "coefficient_version": coeff_version,
                "capture_fraction": capture,
            },
        }

    client_share_frac = clamp_fraction((client_sov_pp or 0) * _SOV_PP_TO_FRACTION)
    comp_share_frac = clamp_fraction((comp_sov_pp or 0) * _SOV_PP_TO_FRACTION)

    rev_usd = normalize_currency(client_rev, currency, "USD", fx_rates)
    if rev_usd is None and (currency or "USD").upper() != "USD":
        basis = "hybrid" if basis == "actual" else basis
    rev_usd = rev_usd if rev_usd is not None else client_rev

    if client_share_frac and client_share_frac > 0:
        addressable = rev_usd / client_share_frac
    else:
        addressable = rev_usd

    at_risk = revenue_at_risk(
        client_revenue=rev_usd,
        competitor_share=comp_share_frac,
        client_share=client_share_frac,
    )
    opp = revenue_opportunity(addressable_revenue=addressable, client_share=client_share_frac or 0)

    inputs = {
        "basis_reason": f"{basis}: " + ("GA4 actuals" if has_ga4 else "modeled AOV*CR") + (", GSC demand" if has_gsc else ", no GSC"),
        "coefficient_version": coeff_version,
        "capture_fraction": capture,
        "estimated_ctr_pct": ctr_pct,
        "conversion_rate_pct": cr_pct,
        "average_order_value": aov,
        "currency": currency,
        "impressions": impressions,
        "client_sov_pp": client_sov_pp,
        "primary_competitor_sov_pp": comp_sov_pp,
        "ga4_revenue_actual": ga4_rev,
        "revenue_client_currency": client_rev,
        "revenue_usd": rev_usd,
        "fx_rate": (fx_rates or {}).get(((currency or "USD").upper(), "USD")),
    }
    if modeled:
        inputs.update({
            "expected_clicks": modeled["expected_clicks"],
            "ai_reachable_clicks": modeled["ai_reachable_clicks"],
            "client_clicks": modeled["client_clicks"],
            "conversions": modeled["conversions"],
        })

    return {
        "revenue_at_risk_usd": at_risk,
        "revenue_opportunity_usd": opp,
        "revenue_basis": basis,
        "revenue_inputs": inputs,
    }


# ---- Tier 2: per-asset attribution math (pure, deterministic; the ONLY home for this arithmetic) ----

def lag_penalty(days_lag, *, max_days: int = 90, floor: float = 0.4) -> float:
    try:
        d = float(days_lag)
    except (TypeError, ValueError):
        return 1.0
    if d <= 0:
        return 1.0
    if d >= max_days:
        return floor
    return 1.0 - (1.0 - floor) * (d / max_days)


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


def allocate_cluster_revenue(total_usd, eligible_asset_ids: list[str],
                             page_attributed: dict[str, float]) -> dict[str, dict]:
    """Deterministic cluster-level allocation (PRD §6 / PLAN §3.4, v1 rule).
    Page-level actuals pass through unclamped (channel actuals are never rewritten to fit the
    Tier-1 total); the remaining pool = max(total - sum(max(page, 0)), 0) goes to the single
    eligible (published) asset when there is exactly one, else equal split. Returns
    {asset_id: {"usd": float, "basis_reason": str}}."""
    out: dict[str, dict] = {}
    page_sum = 0.0
    for aid, usd in (page_attributed or {}).items():
        try:
            val = float(usd)
        except (TypeError, ValueError):
            continue
        out[aid] = {"usd": val, "basis_reason": "page_level_actual"}
        page_sum += max(val, 0.0)
    try:
        total = float(total_usd)
    except (TypeError, ValueError):
        total = 0.0
    pool = max(total - page_sum, 0.0)
    eligible = [aid for aid in (eligible_asset_ids or []) if aid not in out]
    if not eligible:
        return out
    if len(eligible) == 1:
        out[eligible[0]] = {"usd": pool, "basis_reason": "dominant_published_asset"}
        return out
    share = pool / len(eligible)
    reason = f"equal_split_{len(eligible)}_assets"
    for aid in eligible:
        out[aid] = {"usd": share, "basis_reason": reason}
    return out
