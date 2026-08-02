from __future__ import annotations

from collections.abc import Iterable

from aivc.reporting.models import (
    ClientActionGroup,
    ClientHeadlineMetric,
    ClientPresentation,
    ClientPriority,
    ClientTopicBrief,
    DecisionCard,
    ReconRecommendationView,
    ReconSignalView,
    SovCompanyPosition,
    TopicPerformance,
)


def _sov(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value:.2f}%"


def _topic_status(topic: TopicPerformance, client_name: str) -> str:
    if topic.current_client_sov is None or topic.current_client_sov <= 0:
        return "Open visibility opportunity"
    if topic.leader_name == client_name or topic.gap_to_leader == 0:
        return "Market-leading position"
    if topic.gap_to_leader is not None and topic.gap_to_leader <= 5:
        return "Within reach"
    if topic.gap_to_leader is not None and topic.gap_to_leader <= 15:
        return "Competitive gap"
    return "Priority visibility gap"


def _tracked_positions(
    topic: TopicPerformance, client_name: str
) -> list[SovCompanyPosition]:
    selected: list[SovCompanyPosition] = []
    seen: set[str] = set()
    for position in topic.positions:
        is_leader = position.company_name == topic.leader_name
        is_client = position.is_client or position.company_name == client_name
        if not (is_leader or is_client or position.is_tracked):
            continue
        key = position.company_name.casefold()
        if key not in seen:
            selected.append(position)
            seen.add(key)
        if len(selected) >= 5:
            break
    return sorted(selected, key=lambda item: item.rank or 1_000_000)


def _topic_interpretation(topic: TopicPerformance, client_name: str) -> str:
    if topic.current_client_sov is None:
        return f"No reliable current SOV measurement is available for {client_name}."
    if topic.current_client_sov <= 0:
        leader = topic.leader_name or "the current market leader"
        return (
            f"{client_name} has no measurable SOV in this monitored topic, while "
            f"{leader} leads at {_sov(topic.leader_sov)}. This is a visibility-building "
            "opportunity rather than evidence of business-market share."
        )
    if topic.leader_name == client_name or topic.gap_to_leader == 0:
        return (
            f"{client_name} leads this monitored topic at {_sov(topic.current_client_sov)} "
            "SOV. The priority is to defend the evidence and citations supporting that position."
        )
    rank_context = (
        f" and ranks #{topic.client_rank} of {topic.market_size}"
        if topic.client_rank
        else ""
    )
    return (
        f"{client_name} holds {_sov(topic.current_client_sov)} SOV"
        f"{rank_context}. "
        f"It trails {topic.leader_name or 'the leader'} by {_sov(topic.gap_to_leader)}."
    )


def _topic_briefs(
    topics: list[TopicPerformance],
    recommendations: list[ReconRecommendationView],
    *,
    client_name: str,
) -> list[ClientTopicBrief]:
    result: list[ClientTopicBrief] = []
    for rank, topic in enumerate(topics[:5], start=1):
        matching = next(
            (
                item
                for item in recommendations
                if item.cluster_id == topic.cluster_id
                or item.cluster_name == topic.cluster_label
            ),
            None,
        )
        position_takeaway = (
            f"{client_name} currently holds the leading measured position. "
            if topic.leader_name == client_name or topic.gap_to_leader == 0
            else (
                f"The current measured gap to {topic.leader_name or 'the leader'} is "
                f"{_sov(topic.gap_to_leader)}. "
            )
        )
        takeaway = position_takeaway + (
                f"Historical Recon evidence also identified movement involving "
                f"{matching.competitor or 'a tracked competitor'}; its proposed cause "
                "should be treated as a working hypothesis."
                if matching
                else "Prioritize authoritative, citable proof that directly addresses this topic."
        )
        result.append(
            ClientTopicBrief(
                priority_rank=rank,
                cluster_id=topic.cluster_id,
                cluster_label=(
                    "General brand and advisory visibility"
                    if topic.cluster_label == "Custom Query"
                    else topic.cluster_label
                ),
                status=_topic_status(topic, client_name),
                client_sov=topic.current_client_sov,
                client_rank=topic.client_rank,
                market_size=topic.market_size,
                leader_name=topic.leader_name,
                leader_sov=topic.leader_sov,
                gap_to_leader=topic.gap_to_leader,
                interpretation=_topic_interpretation(topic, client_name),
                tracked_positions=_tracked_positions(topic, client_name),
                competitive_takeaway=takeaway,
                recommended_focus=(
                    _safe_recon_actions(matching.actions, topic)[:2] if matching else []
                ),
            )
        )
    return result


_CARD_IMPLICATIONS = {
    "competitive_threat": (
        "A measured competitor movement may weaken the client's relative visibility."
    ),
    "visibility_opportunity": (
        "The evidence identifies a practical opportunity to improve AI visibility."
    ),
    "source_opportunity": (
        "A citation-source change creates an opportunity to strengthen authoritative coverage."
    ),
    "defensive_gap": (
        "The current evidence indicates a gap that should be protected from further widening."
    ),
    "watch": "The movement warrants monitoring before a stronger causal conclusion is made.",
}


def _priorities(
    cards: list[DecisionCard],
    recommendations: list[ReconRecommendationView],
    signals: list[ReconSignalView],
    topics: list[TopicPerformance],
) -> list[ClientPriority]:
    limit = 5
    priorities: list[ClientPriority] = []
    seen: set[tuple[str, str]] = set()
    topic_index = {topic.cluster_id: topic for topic in topics}
    for card in cards:
        key = ("citation", card.title.casefold())
        if key in seen:
            continue
        seen.add(key)
        priorities.append(
            ClientPriority(
                priority_rank=len(priorities) + 1,
                source="citation",
                title=card.title,
                topic=card.cluster_label,
                priority=card.priority,
                confidence=card.confidence,
                what_is_happening=card.summary,
                why_it_matters=_CARD_IMPLICATIONS[card.classification],
                working_hypothesis=card.recon_evidence_summary,
                actions=[item.action for item in card.recommended_actions[:3]],
            )
        )
        if len(priorities) >= limit:
            return priorities
    seen_recon_topics: set[str] = set()
    for recommendation in recommendations:
        title = recommendation.cluster_name or "Competitive visibility priority"
        topic_key = recommendation.cluster_id or title.casefold()
        if topic_key in seen_recon_topics:
            continue
        seen_recon_topics.add(topic_key)
        key = (title.casefold(), (recommendation.competitor or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        topic = topic_index.get(recommendation.cluster_id or "")
        signal = next(
            (
                item
                for item in signals
                if item.cluster_id == recommendation.cluster_id
                and (
                    not recommendation.competitor
                    or item.competitor == recommendation.competitor
                )
            ),
            None,
        )
        competitor = recommendation.competitor or (signal.competitor if signal else None)
        movement = (
            f"{competitor or 'A tracked competitor'} recorded a "
            f"{signal.competitor_delta_pp:+.2f}pp monitored SOV movement"
            if signal and signal.competitor_delta_pp is not None
            else f"{competitor or 'A tracked competitor'} recorded a monitored visibility movement"
        )
        current_position = (
            f" The current client position is {_sov(topic.current_client_sov)} SOV."
            if topic
            else ""
        )
        why_it_matters = (
            _topic_interpretation(topic, "The client")
            if topic
            else "The movement may affect how AI systems position the client in this topic."
        )
        priorities.append(
            ClientPriority(
                priority_rank=len(priorities) + 1,
                source="recon",
                title=title,
                topic=recommendation.cluster_name,
                competitor=competitor,
                priority=recommendation.priority or "medium",
                confidence=recommendation.confidence or "unknown",
                what_is_happening=f"{movement}.{current_position}",
                why_it_matters=why_it_matters,
                working_hypothesis=recommendation.probable_cause,
                actions=_safe_recon_actions(
                    recommendation.actions,
                    topic,
                )[:3],
                timeline=recommendation.timeline,
            )
        )
        if len(priorities) >= limit:
            break
    return priorities


_STALE_ACTION_MARKERS = (
    "zero indexed",
    "zero presence",
    "complete absence",
    "currently has no",
    "client has no",
)


def _safe_recon_actions(
    actions: list[str], topic: TopicPerformance | None
) -> list[str]:
    if topic is None or not topic.current_client_sov or topic.current_client_sov <= 0:
        return actions
    return [
        action
        for action in actions
        if not any(marker in action.casefold() for marker in _STALE_ACTION_MARKERS)
    ]


def _dedupe(values: Iterable[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split()).casefold()
        if normalized and normalized not in seen:
            result.append(value)
            seen.add(normalized)
        if len(result) >= limit:
            break
    return result


def _action_plan(
    priorities: list[ClientPriority], topics: list[ClientTopicBrief]
) -> list[ClientActionGroup]:
    immediate = _dedupe(
        (priority.actions[0] for priority in priorities if priority.actions), limit=3
    )
    near_term = _dedupe(
        (
            action
            for priority in priorities
            for action in priority.actions[1:]
        ),
        limit=4,
    )
    monitor = _dedupe(
        (
            f"Track {topic.cluster_label} SOV and the gap to "
            f"{topic.leader_name or 'the market leader'} in the next monitoring cycle."
            for topic in topics[:3]
        ),
        limit=3,
    )
    if not immediate:
        immediate = ["Validate the highest-priority topic and assign an accountable owner."]
    if not near_term:
        near_term = ["Strengthen authoritative content for the highest-priority topic."]
    return [
        ClientActionGroup(horizon="Immediate", actions=immediate),
        ClientActionGroup(horizon="Near term", actions=near_term),
        ClientActionGroup(horizon="Monitor", actions=monitor),
    ]


def build_client_presentation(
    *,
    client_name: str,
    topics: list[TopicPerformance],
    cards: list[DecisionCard],
    recommendations: list[ReconRecommendationView],
    signals: list[ReconSignalView],
) -> ClientPresentation:
    strongest = max(
        (topic for topic in topics if topic.current_client_sov is not None),
        key=lambda item: item.current_client_sov or 0.0,
        default=None,
    )
    largest_gap = max(
        (topic for topic in topics if topic.gap_to_leader is not None),
        key=lambda item: item.gap_to_leader or 0.0,
        default=None,
    )
    topic_briefs = _topic_briefs(
        topics,
        recommendations,
        client_name=client_name,
    )
    priorities = _priorities(
        cards,
        recommendations,
        signals,
        topics,
    )
    visible_topics = sum((topic.current_client_sov or 0.0) > 0 for topic in topics)
    executive_narrative = (
        f"{client_name}'s strongest measured position is "
        f"{strongest.cluster_label if strongest else 'not yet established'} at "
        f"{_sov(strongest.current_client_sov if strongest else None)} SOV. "
        f"The largest current visibility gap is "
        f"{_sov(largest_gap.gap_to_leader if largest_gap else None)} in "
        f"{largest_gap.cluster_label if largest_gap else 'the monitored topic set'}. "
        f"{len(priorities)} priority response(s) are supported by the available evidence."
    )
    main_priority = priorities[0] if priorities else None
    if largest_gap and (largest_gap.gap_to_leader or 0.0) > 0:
        main_implication_title = "Close the largest visibility gap"
        main_implication = _topic_interpretation(largest_gap, client_name)
    elif main_priority:
        main_implication_title = "Act on the leading priority"
        main_implication = main_priority.what_is_happening
    else:
        main_implication_title = "Maintain the baseline"
        main_implication = (
            "No material publishable movement was detected; maintain the monitoring cadence."
        )
    headline_metrics = [
        ClientHeadlineMetric(
            label="Current SOV high",
            value=_sov(strongest.current_client_sov if strongest else None),
            context=strongest.cluster_label if strongest else "No measured topic",
        ),
        ClientHeadlineMetric(
            label="Largest leader gap",
            value=_sov(largest_gap.gap_to_leader if largest_gap else None),
            context=largest_gap.cluster_label if largest_gap else "No comparable topic",
        ),
        ClientHeadlineMetric(
            label="Visible topics",
            value=str(visible_topics),
            context="Topics with measurable current SOV",
        ),
        ClientHeadlineMetric(
            label="Competitive priorities",
            value=str(len(priorities)),
            context="Evidence-backed responses for this cycle",
        ),
    ]
    leadership_decisions = [
        (
            f"Decide whether {topic.cluster_label} should receive priority based on "
            f"its {_sov(topic.gap_to_leader)} gap to "
            f"{topic.leader_name or 'the market leader'}."
        )
        for topic in topic_briefs[:3]
    ]
    if main_priority:
        leadership_decisions.append(
            f"Assign ownership and resources for the response to {main_priority.title}."
        )
    conclusion_topic = largest_gap.cluster_label if largest_gap else "its priority topics"
    strategic_conclusion = (
        f"The next reporting cycle should judge progress by whether {client_name} narrows "
        f"the measured gap in {conclusion_topic} "
        "and earns stronger, more authoritative AI citations."
    )
    return ClientPresentation(
        executive_narrative=executive_narrative,
        main_implication_title=main_implication_title,
        main_implication=main_implication,
        headline_metrics=headline_metrics,
        topics=topic_briefs,
        priorities=priorities,
        action_plan=_action_plan(priorities, topic_briefs),
        leadership_decisions=leadership_decisions,
        strategic_conclusion=strategic_conclusion,
    )
