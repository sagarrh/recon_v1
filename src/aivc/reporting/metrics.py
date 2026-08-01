from __future__ import annotations

from aivc.reporting.models import MetricDefinition

METRIC_DEFINITIONS: dict[str, MetricDefinition] = {
    "literal_answer_visibility": MetricDefinition(
        metric_name="literal_answer_visibility",
        display_name="Literal answer visibility",
        definition=(
            "Distinct monitored answers containing a boundary-aware company alias divided "
            "by all valid answers in the same scope."
        ),
        unit="ratio",
        source_owner="ai_visibility",
        methodology_version="boundary_literal_alias_v1",
        aggregation_scope="provider/query or explicitly disclosed latest-query cohort",
        higher_is_better=True,
    ),
    "citation_answer_coverage": MetricDefinition(
        metric_name="citation_answer_coverage",
        display_name="Citation answer coverage",
        definition="Distinct answers citing a URL divided by valid answers in the same run.",
        unit="ratio",
        source_owner="ai_visibility",
        methodology_version="answer_url_coverage_v1",
        aggregation_scope="provider/query/run",
        higher_is_better=None,
    ),
    "owned_source_coverage": MetricDefinition(
        metric_name="owned_source_coverage",
        display_name="Owned source coverage",
        definition="Citation coverage attributable to a domain owned by the client.",
        unit="ratio",
        source_owner="ai_visibility",
        methodology_version="official_domain_v1",
        aggregation_scope="provider/query/run",
        higher_is_better=True,
    ),
    "weekly_cluster_sov": MetricDefinition(
        metric_name="weekly_cluster_sov",
        display_name="Weekly cluster share of voice",
        definition="Weekly Recon share-of-voice observation for one company and topic cluster.",
        unit="percentage_points",
        source_owner="scout",
        methodology_version="recon_weekly_sov_v1",
        aggregation_scope="client/cluster/week",
        higher_is_better=True,
    ),
    "sov_change_vs_baseline": MetricDefinition(
        metric_name="sov_change_vs_baseline",
        display_name="SOV change versus baseline",
        definition="Current cluster SOV minus the available rolling baseline, in points.",
        unit="percentage_points",
        source_owner="scout",
        methodology_version="recon_rolling_baseline_v1",
        aggregation_scope="company/cluster/week",
        higher_is_better=None,
    ),
    "provider_query_visibility": MetricDefinition(
        metric_name="provider_query_visibility",
        display_name="Provider/query visibility",
        definition="Literal answer visibility isolated to one provider and monitoring query.",
        unit="ratio",
        source_owner="ai_visibility",
        methodology_version="boundary_literal_alias_v1",
        aggregation_scope="provider/query/run",
        higher_is_better=True,
    ),
}


def metric_definitions() -> list[MetricDefinition]:
    return [METRIC_DEFINITIONS[name] for name in sorted(METRIC_DEFINITIONS)]
