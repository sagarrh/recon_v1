from __future__ import annotations

from typing import Any


def choose_hypothesis(comparison: dict[str, Any]) -> dict[str, Any]:
    strengthened_pages = [
        source
        for source in comparison.get("page_source_evidence", [])
        if source.get("page_diff_strengthened")
    ]
    if strengthened_pages:
        best_score = max(
            int(source.get("attribution", {}).get("score", 0)) for source in strengthened_pages
        )
        return {
            "type": "direct_page_content_change",
            "confidence": "high" if best_score >= 70 else "medium",
            "inference": (
                "A temporally aligned historical page snapshot added or strengthened "
                "the client, making the page change a likely contributor."
            ),
        }
    recommendation = comparison.get("recommendation_pattern")
    if recommendation:
        return {
            "type": "recommendation_pattern_shift",
            "confidence": "medium",
            "inference": (
                "The provider shifted toward a repeated recommendation bundle; "
                "the client benefited from that broader source/recommendation pattern."
            ),
        }
    material_sources = [
        item
        for item in comparison.get("citation_deltas", [])
        if item.get("status") == "new" and int(item.get("current_answer_coverage", 0)) >= 2
    ]
    if material_sources:
        return {
            "type": "broader_source_portfolio_shift",
            "confidence": "low",
            "inference": (
                "New sources are associated with the visibility change, but direct "
                "causation is not established."
            ),
        }
    return {
        "type": "insufficient_evidence",
        "confidence": "low",
        "inference": "The stored evidence does not isolate a direct driver.",
    }
