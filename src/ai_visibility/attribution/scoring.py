from __future__ import annotations

from typing import Any


def score_page_candidate(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    components: list[dict[str, Any]] = []

    def add(name: str, points: int, applies: bool) -> None:
        if applies:
            components.append({"component": name, "points": points})

    add("page_mentions_company", 20, bool(evidence.get("page_mentions_company")))
    add("company_in_title_or_heading", 10, bool(evidence.get("company_in_heading")))
    add("links_to_official_domain", 10, bool(evidence.get("links_to_official_domain")))
    add("publisher_is_company", 10, bool(evidence.get("publisher_is_company")))
    add("page_diff_strengthened_company", 20, bool(evidence.get("page_diff_strengthened")))
    add(
        "url_new_or_materially_expanded",
        10,
        evidence.get("status") == "new" or abs(int(evidence.get("coverage_delta", 0))) >= 2,
    )
    add("positive_association_lift", 10, float(evidence.get("association_lift", 0)) >= 0.1)
    add("semantic_alignment", 10, bool(evidence.get("semantic_alignment")))
    add("repeated_evidence", 10, bool(evidence.get("repeated_evidence")))
    add(
        "page_does_not_mention_company",
        -25,
        evidence.get("page_mentions_company") is False and not evidence.get("publisher_is_company"),
    )
    coverage = int(evidence.get("current_answer_coverage", 0))
    total = int(evidence.get("current_answer_count", 0))
    add("ubiquitous_source", -20, total > 0 and coverage / total >= 0.8)
    add(
        "historical_snapshot_unavailable",
        -15,
        not bool(evidence.get("historical_snapshot_available")),
    )
    add("citation_positions_unavailable", -10, bool(evidence.get("positions_unavailable")))
    add(
        "configuration_incomplete",
        -15,
        bool(evidence.get("configuration_incomplete")),
    )
    add("single_answer_evidence", -10, coverage == 1)
    add("metric_mismatch", -10, bool(evidence.get("metric_mismatch")))
    score = max(0, min(100, sum(int(item["points"]) for item in components)))
    confidence = "high" if score >= 70 else "medium" if score >= 45 else "low"
    return {"score": score, "confidence": confidence, "components": components}


def classify_url_relationship(delta: dict[str, Any], current_answer_count: int) -> str:
    coverage = int(delta.get("current_answer_coverage", 0))
    previous_coverage = int(delta.get("previous_answer_coverage", 0))
    lift = float(delta.get("current_association_lift", 0.0))
    if delta.get("status") == "new" and coverage == 1:
        return "localized_new_owned_source"
    if (
        previous_coverage > 0
        and current_answer_count > 0
        and coverage / current_answer_count >= 0.8
        and abs(lift) < 0.1
    ):
        return "supporting_or_topic_context"
    if delta.get("status") == "new" and coverage >= 2 and lift >= 0.1:
        return "new_source_candidate"
    return "associated_source"
