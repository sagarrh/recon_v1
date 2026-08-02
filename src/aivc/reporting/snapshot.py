from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from aivc.contracts.models import AnalysisPeriod, SignalBundle, stable_id
from aivc.reporting.cards import build_decision_cards
from aivc.reporting.client_presentation import build_client_presentation
from aivc.reporting.config import ResolvedReportConfig
from aivc.reporting.metrics import metric_definitions
from aivc.reporting.models import (
    EvidenceIndexItem,
    ExcludedTopic,
    FinalReportSnapshot,
    FinalReportStatus,
    ProviderPerformance,
    QueryPerformance,
    ReconRecommendationView,
    ReconReportingPayload,
    ReconRunHistory,
    ReconSignalView,
    ReportConfigMetadata,
    ReportMetric,
    SovCompanyPosition,
    SovHistoryPoint,
    TopicPerformance,
)
from aivc.reporting.quality import assess_publication

_LIMITATION_TEXT = {
    "citation_positions_unavailable": (
        "Some providers did not supply usable citation offsets, so citation position is "
        "not compared."
    ),
    "company_metric_mismatch": (
        "Literal company counts differ from an upstream metric in at least one run; both "
        "remain auditable."
    ),
    "execution_configuration_incomplete": (
        "Some provider execution settings were incomplete, reducing comparison confidence."
    ),
    "failed_monitoring_run": (
        "Zero-answer monitoring runs were retained but excluded from baselines."
    ),
    "historical_page_version_unavailable": (
        "Historical page versions are unavailable for some sources, so page causation "
        "is not claimed."
    ),
}


def _provider_summary(
    citation_report: dict[str, Any], config: ResolvedReportConfig
) -> list[ProviderPerformance]:
    rows = citation_report.get("provider_intelligence", [])
    if not isinstance(rows, list):
        return []
    result: list[ProviderPerformance] = []
    for row in rows[: config.profile_settings.max_provider_rows]:
        if not isinstance(row, dict):
            continue
        current = row.get("latest_visibility")
        result.append(
            ProviderPerformance(
                provider=str(row.get("provider") or "unknown"),
                current_visibility=float(current) if current is not None else None,
                trend=str(row.get("trend") or "unknown"),
                query_count=int(row.get("query_count") or 0),
                metrics=[
                    ReportMetric(
                        metric_name="literal_answer_visibility",
                        value=float(current) if current is not None else None,
                        unit="ratio",
                        numerator=row.get("latest_literal_answer_count"),
                        denominator=row.get("latest_total_answer_count"),
                        provider=str(row.get("provider") or "unknown"),
                    )
                ],
            )
        )
    return result


def _executive_metrics(citation_report: dict[str, Any]) -> list[ReportMetric]:
    overall = citation_report.get("overall_visibility", {})
    if not isinstance(overall, dict):
        return []
    value = overall.get("latest_weighted_visibility")
    return [
        ReportMetric(
            metric_name="literal_answer_visibility",
            value=float(value) if value is not None else None,
            unit="ratio",
            numerator=overall.get("latest_literal_answer_count"),
            denominator=overall.get("latest_total_answer_count"),
            quality_flags=["latest_query_cohort"],
        )
    ]


def _query_details(
    citation_report: dict[str, Any],
    config: ResolvedReportConfig,
    *,
    excluded_cluster_ids: set[str] | None = None,
) -> list[QueryPerformance]:
    if not config.profile_settings.include_query_details:
        return []
    rows = citation_report.get("query_intelligence", [])
    if not isinstance(rows, list):
        return []
    result: list[QueryPerformance] = []
    excluded = excluded_cluster_ids or set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("cluster_id") or "") in excluded:
            continue
        result.append(
            QueryPerformance(
                monitor_query_key=str(row.get("monitor_query_key") or "unknown"),
                query=str(row.get("query") or "Unknown query"),
                provider=str(row.get("provider") or "unknown"),
                method=str(row.get("method") or "") or None,
                cluster_id=str(row.get("cluster_id") or "") or None,
                cluster_name=str(row.get("cluster_name") or "") or None,
                current_visibility=(
                    float(row["current_visibility"])
                    if row.get("current_visibility") is not None
                    else None
                ),
                latest_delta=(
                    float(row["latest_delta"])
                    if row.get("latest_delta") is not None
                    else None
                ),
                trend=str(row.get("trend") or "unknown"),
                run_count=int(row.get("run_count") or 0),
            )
        )
        if len(result) >= config.profile_settings.max_query_rows:
            break
    return result


def _malformed_topics(recon: ReconReportingPayload) -> tuple[set[str], list[ExcludedTopic]]:
    identifiers: set[str] = set()
    excluded: list[ExcludedTopic] = []
    for cluster in recon.clusters:
        raw_name = str(cluster.get("cluster_name") or "")
        cluster_id = str(cluster.get("cluster_id") or "")
        if raw_name and raw_name != raw_name.strip():
            identifiers.add(cluster_id)
            excluded.append(
                ExcludedTopic(
                    cluster_id=cluster_id or None,
                    cluster_name=raw_name.strip(),
                    reason="Malformed source cluster label requires client-data review.",
                )
            )
    return identifiers, excluded


def _topic_summary(
    cards: list[Any],
    recon: ReconReportingPayload,
    config: ResolvedReportConfig,
) -> tuple[list[TopicPerformance], list[ExcludedTopic]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for card in cards:
        grouped[card.cluster_id].append(card)
    malformed_ids, excluded = _malformed_topics(recon)
    market = recon.sov.get("market_snapshot", [])
    history = recon.sov.get("history", [])
    if not isinstance(market, list):
        market = []
    if not isinstance(history, list):
        history = []
    market_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for value in market:
        if not isinstance(value, dict):
            continue
        cluster_id = str(value.get("cluster_id") or "")
        cluster_name = str(value.get("cluster_name") or "Custom Query").strip()
        if cluster_id in malformed_ids:
            continue
        market_groups[(cluster_id, cluster_name)].append(value)

    client_name = str(recon.client.get("client_name") or "")
    tracked_names = {
        client_name.casefold(),
        *{
            str(name).casefold()
            for name in recon.client.get("competitors", [])
            if str(name).strip()
        },
    }
    rows: list[TopicPerformance] = []
    for (cluster_id, cluster_label), observations in sorted(market_groups.items()):
        weeks = [str(item.get("week_date") or "") for item in observations]
        latest_week = max(weeks, default="")
        latest = [item for item in observations if str(item.get("week_date") or "") == latest_week]
        latest.sort(
            key=lambda item: (
                int(item.get("rank") or 1_000_000),
                -float(item.get("sov") or 0.0),
                str(item.get("company_name") or ""),
            )
        )
        all_positions = [
            SovCompanyPosition(
                company_name=str(item.get("company_name") or "Unknown"),
                sov=float(item.get("sov") or 0.0),
                rank=int(item["rank"]) if item.get("rank") is not None else None,
                is_client=bool(item.get("is_client", False)),
                is_tracked=str(item.get("company_name") or "").casefold() in tracked_names,
            )
            for item in latest
        ]
        client_position = next((item for item in all_positions if item.is_client), None)
        leader = all_positions[0] if all_positions else None
        positions = all_positions
        if not config.profile_settings.include_full_sov_tables:
            selected = positions[:5]
            selected_names = {item.company_name.casefold() for item in selected}
            selected.extend(
                item
                for item in positions
                if (item.is_client or item.is_tracked)
                and item.company_name.casefold() not in selected_names
            )
            positions = selected
        card_items = grouped.get(cluster_id, [])
        literal_values = [
            metric.value
            for card in card_items
            for metric in card.client_metrics
            if metric.metric_name == "provider_query_visibility" and metric.value is not None
        ]
        history_points: list[SovHistoryPoint] = []
        seen_client_weeks: set[str] = set()
        for item in history:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("cluster_id") or "")
            item_name = str(item.get("cluster_name") or "Custom Query").strip()
            if item_id != cluster_id or item_name != cluster_label:
                continue
            week = str(item.get("week_date") or "")
            if week not in seen_client_weeks:
                history_points.append(
                    SovHistoryPoint(
                        week_date=week,
                        company_name=client_name,
                        sov=float(item.get("client_sov_this_week") or 0.0),
                        is_client=True,
                        answers_analyzed=(
                            int(item["answers_analyzed"])
                            if item.get("answers_analyzed") is not None
                            else None
                        ),
                        platforms=[str(value) for value in item.get("platforms_analyzed", [])],
                    )
                )
                seen_client_weeks.add(week)
            history_points.append(
                SovHistoryPoint(
                    week_date=week,
                    company_name=str(item.get("competitor_name") or "Unknown"),
                    sov=float(item.get("current_sov") or 0.0),
                    delta_pp=(
                        float(item["sov_delta_pp"])
                        if item.get("sov_delta_pp") is not None
                        else None
                    ),
                    alert_flag=str(item.get("alert_flag") or "") or None,
                    answers_analyzed=(
                        int(item["answers_analyzed"])
                        if item.get("answers_analyzed") is not None
                        else None
                    ),
                    platforms=[str(value) for value in item.get("platforms_analyzed", [])],
                )
            )
        rows.append(
            TopicPerformance(
                cluster_id=cluster_id,
                cluster_label=cluster_label,
                current_literal_visibility=(
                    sum(float(value) for value in literal_values) / len(literal_values)
                    if literal_values
                    else None
                ),
                current_client_sov=client_position.sov if client_position else None,
                provider_count=len({card.provider for card in card_items if card.provider}),
                query_count=len({card.query for card in card_items if card.query}),
                as_of_week=latest_week or None,
                client_rank=client_position.rank if client_position else None,
                market_size=len(latest),
                leader_name=leader.company_name if leader else None,
                leader_sov=leader.sov if leader else None,
                gap_to_leader=(
                    max(0.0, leader.sov - client_position.sov)
                    if leader and client_position
                    else None
                ),
                positions=positions,
                history=history_points,
                warnings=sorted(
                    {warning for card in card_items for warning in card.warnings}
                ),
            )
        )
    rows.sort(
        key=lambda item: (
            -(item.gap_to_leader or 0.0),
            -(item.current_client_sov or 0.0),
            item.cluster_label,
        )
    )
    return rows, excluded


def _recon_views(
    recon: ReconReportingPayload,
    config: ResolvedReportConfig,
    *,
    excluded_cluster_ids: set[str] | None = None,
) -> tuple[list[ReconSignalView], list[ReconRecommendationView], list[ReconRunHistory]]:
    signals: list[ReconSignalView] = []
    excluded = excluded_cluster_ids or set()
    for item in recon.signals:
        cluster_id = str(item.get("cluster_id") or "")
        if cluster_id in excluded:
            continue
        if bool(item.get("noise")) or str(item.get("triage_severity") or "").upper() == "NOISE":
            continue
        evidence = item.get("evidence", {})
        summary = None
        if isinstance(evidence, dict):
            for evidence_key in (
                "ai_citation_changes",
                "third_party_signals",
                "website_changes",
            ):
                candidate = evidence.get(evidence_key)
                if isinstance(candidate, dict):
                    summary = str(
                        candidate.get("delta_summary")
                        or candidate.get("summary")
                        or ""
                    ) or None
                    if summary:
                        break
        signals.append(
            ReconSignalView(
                signal_id=str(item.get("id") or "unknown"),
                cluster_id=cluster_id or None,
                cluster_name=str(item.get("cluster_name") or "") or None,
                week_date=str(item.get("week_date") or "") or None,
                severity=str(item.get("triage_severity") or "") or None,
                competitor=str(item.get("primary_competitor") or "") or None,
                competitor_delta_pp=(
                    float(item["primary_competitor_delta_pp"])
                    if item.get("primary_competitor_delta_pp") is not None
                    else None
                ),
                evidence_summary=summary,
            )
        )
    signal_limit = (
        len(signals)
        if config.profile_settings.include_full_sov_tables
        else config.profile_settings.max_decision_cards
    )
    recommendations: list[ReconRecommendationView] = []
    publishable_cluster_ids = {
        signal.cluster_id for signal in signals if signal.cluster_id is not None
    }
    seen: set[tuple[str, str, str]] = set()
    for item in recon.recommendations:
        cluster_id = str(item.get("cluster_id") or "")
        if cluster_id not in publishable_cluster_ids:
            continue
        if str(item.get("triage_severity") or "").upper() == "NOISE":
            continue
        recommendation_key = (
            cluster_id,
            str(item.get("competitor_name") or ""),
            str(item.get("summary") or ""),
        )
        if recommendation_key in seen:
            continue
        seen.add(recommendation_key)
        recommendations.append(
            ReconRecommendationView(
                recommendation_id=str(item.get("id") or "unknown"),
                cluster_id=recommendation_key[0] or None,
                cluster_name=str(item.get("cluster_name") or "") or None,
                competitor=recommendation_key[1] or None,
                priority=str(item.get("priority") or "") or None,
                confidence=str(item.get("confidence") or "") or None,
                summary=recommendation_key[2] or None,
                probable_cause=str(item.get("probable_cause") or "") or None,
                gap_analysis=str(item.get("gap_analysis") or "") or None,
                actions=[str(value) for value in item.get("action_bullets", [])],
                timeline=str(item.get("timeline") or "") or None,
            )
        )
        if len(recommendations) >= config.profile_settings.max_recommendations:
            break
    runs = [
        ReconRunHistory(
            run_id=str(item.get("run_id") or "unknown"),
            status=str(item.get("status") or "unknown"),
            sync_date=str(item.get("sync_date") or "") or None,
            started_at=item.get("started_at"),
            triggers_fired=int(item.get("triggers_fired") or 0),
        )
        for item in recon.run_history
    ]
    return signals[:signal_limit], recommendations, runs


def build_final_report_snapshot(
    *,
    parent_run_id: UUID,
    citation_report: dict[str, Any],
    citation_bundle: SignalBundle,
    recon_bundle: SignalBundle,
    recon_reporting: dict[str, Any],
    config: ResolvedReportConfig,
) -> FinalReportSnapshot:
    publication = assess_publication(citation_bundle, recon_bundle)
    client = citation_bundle.client
    recon_report = ReconReportingPayload.model_validate(recon_reporting)
    if str(recon_report.client.get("client_id")) != str(client.client_id):
        raise ValueError("Recon reporting payload belongs to a different client.")
    cards = build_decision_cards(
        publication,
        client_id=client.client_id,
        client_name=client.canonical_name,
        profile=config.profile_settings,
    )
    consolidated = []
    seen_actions: set[str] = set()
    for card in cards:
        for action in card.recommended_actions:
            if action.action_id not in seen_actions:
                consolidated.append(action)
                seen_actions.add(action.action_id)
            if len(consolidated) >= config.profile_settings.max_recommendations:
                break
        if len(consolidated) >= config.profile_settings.max_recommendations:
            break

    flags = sorted(set(publication.quality_flags))
    limitations = [_LIMITATION_TEXT.get(flag, flag.replace("_", " ")) for flag in flags]
    limitations.extend(blocker.replace("_", " ") for blocker in publication.blockers)
    status = (
        FinalReportStatus.blocked
        if publication.blockers
        else FinalReportStatus.partial
        if flags
        else FinalReportStatus.complete
    )
    headline = (
        f"{client.canonical_name}: {len(cards)} material AI visibility priorities"
        if cards
        else f"{client.canonical_name}: no material publishable movement detected"
    )
    topics, excluded_topics = _topic_summary(cards, recon_report, config)
    excluded_cluster_ids = {
        topic.cluster_id for topic in excluded_topics if topic.cluster_id is not None
    }
    recon_signals, recon_recommendations, recon_runs = _recon_views(
        recon_report,
        config,
        excluded_cluster_ids=excluded_cluster_ids,
    )
    measured_topics = sum(topic.current_client_sov is not None for topic in topics)
    executive_summary = str(recon_report.executive_summary or "").strip() or (
        f"{client.canonical_name} has measured SOV across {measured_topics} topic snapshot(s). "
        f"{len(cards)} material citation finding(s) passed the publication gate."
    )
    used_evidence = sorted({ref for card in cards for ref in card.evidence_refs})
    evidence_index = []
    for evidence_id in used_evidence:
        artifact = publication.evidence[evidence_id]
        payload = artifact.payload
        summary = str(
            payload.get("triage_reason")
            or payload.get("query")
            or payload.get("alert_reason")
            or ""
        ) or None
        evidence_index.append(
            EvidenceIndexItem(
                evidence_id=evidence_id,
                kind=artifact.kind,
                source_ref=artifact.source_ref,
                summary=summary,
            )
        )

    source_checksums = {
        citation_bundle.bundle_id: str(citation_bundle.checksum),
        recon_bundle.bundle_id: str(recon_bundle.checksum),
    }
    idempotency_key = stable_id("final-report", parent_run_id)
    starts = [
        period
        for period in (citation_bundle.analysis_period.start, recon_bundle.analysis_period.start)
        if period is not None
    ]
    ends = [
        period
        for period in (citation_bundle.analysis_period.end, recon_bundle.analysis_period.end)
        if period is not None
    ]
    snapshot = FinalReportSnapshot(
        report_id=stable_id("final-report", idempotency_key),
        idempotency_key=idempotency_key,
        parent_run_id=parent_run_id,
        client=client,
        config=ReportConfigMetadata(
            config_version=config.config.config_version,
            report_config_hash=config.config_hash,
            source=config.source,
            effective_profile=config.profile_settings,
        ),
        source_bundle_ids=[citation_bundle.bundle_id, recon_bundle.bundle_id],
        source_bundle_checksums=source_checksums,
        analysis_period=AnalysisPeriod(
            start=min(starts) if starts else None,
            end=max(ends) if ends else None,
        ),
        status=status,
        headline=headline,
        executive_summary=executive_summary,
        executive_metrics=_executive_metrics(citation_report),
        provider_summary=_provider_summary(citation_report, config),
        topic_summary=topics,
        query_details=_query_details(
            citation_report,
            config,
            excluded_cluster_ids=excluded_cluster_ids,
        ),
        recon_signals=recon_signals,
        recon_recommendations=recon_recommendations,
        recon_run_history=recon_runs,
        excluded_topics=excluded_topics,
        # The full Recon SQL result remains in the database ledger. Final reports retain
        # only the compact, validated views required for publication.
        recon_reporting=None,
        client_presentation=build_client_presentation(
            client_name=client.canonical_name,
            topics=topics,
            cards=cards,
            recommendations=recon_recommendations,
            signals=recon_signals,
        ),
        decision_cards=cards,
        consolidated_actions=consolidated,
        data_quality_flags=flags + list(publication.blockers),
        limitations=limitations,
        methodology=metric_definitions(),
        evidence_index=evidence_index,
    ).sealed()
    snapshot.verify_checksum()
    return snapshot
