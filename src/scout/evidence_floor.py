# evidence_floor.py — Deterministic per-source minimum-evidence floors for the investigation nodes.
# Purpose: Count the structured evidence each source gathered so a below-floor source can skip the LLM (R3-1), enrich (R3-2), or abstain (R3-3).
# Scope: Pure functions over already-fetched evidence (page diff, SERP rows, AI bundle); no LLM, no I/O.
# Consumers: scout/nodes/website_diff.py, web_intelligence.py, ai_response_analysis.py (floor gate + post-enrich recount); scout/nodes/enrichment.py.


def website_changed_elements(page_diff: dict | None, new_pages: list, schema_added: list, content_themes: list) -> int:
    """Count deterministic 'changed elements' for a website diff: sentence churn + new pages + schema + themes.
    Drives the pre-LLM evidence floor; 0 when there is no diff and no pre-fetched structural signal."""
    churn = (page_diff.get("sentences_added", 0) + page_diff.get("sentences_removed", 0)) if page_diff else 0
    return churn + len(new_pages or []) + len(schema_added or []) + len(content_themes or [])


def serp_usable_rows(serp_results: list[dict]) -> int:
    """Count SERP result rows that carry at least one non-empty snippet (title or snippet text).
    A row with an empty/blank snippets list contributes nothing usable to extraction."""
    count = 0
    for r in serp_results or []:
        for s in r.get("snippets", []) or []:
            if s.get("title") or s.get("snippet"):
                count += 1
                break
    return count


def ai_paired_weeks(bundle: dict | None) -> int:
    """Count comparable week-over-week pairs in an AI-response bundle = min(current_responses, baseline_responses).
    0 when the bundle is one-sided (only current OR only baseline), which is below the default paired-weeks floor."""
    if not bundle:
        return 0
    return min(len(bundle.get("current_responses", []) or []), len(bundle.get("baseline_responses", []) or []))


def below_floor(count: int, threshold: int) -> bool:
    """Return True when an evidence count is strictly below its configured minimum floor.
    Single definition shared by the floor gate (R3-1) and the post-enrichment recount (R3-2)."""
    return count < threshold
