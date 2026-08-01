from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from ai_visibility.companies.aliases import aliases_for
from ai_visibility.scraping.fetcher import FetchResult
from ai_visibility.utils.text import context_snippets, literal_mentions
from ai_visibility.utils.urls import domain_from_url


def analyze_page_company(
    result: FetchResult, company_name: str, official_domains: list[str]
) -> dict[str, Any]:
    normalized_official_domains = {
        domain.casefold().removeprefix("www.") for domain in official_domains
    }
    aliases = aliases_for(company_name)
    mention_count, _ = literal_mentions(result.main_text, aliases)
    title_count, _ = literal_mentions(result.title or "", aliases)
    heading_count = sum(literal_mentions(heading, aliases)[0] for heading in result.headings)
    linked_domains: set[str] = set()
    for link in result.links:
        try:
            linked_domains.add(domain_from_url(link).removeprefix("www."))
        except ValueError:
            continue
    publisher = domain_from_url(result.final_url).removeprefix("www.")
    return {
        "company": company_name,
        "mention_count": mention_count,
        "mentioned_in_title": title_count > 0,
        "mentioned_in_heading": heading_count > 0,
        "links_to_official_domain": bool(linked_domains & normalized_official_domains),
        "publisher_is_company": publisher in normalized_official_domains,
        "mention_role": (
            "publisher_identity"
            if publisher in normalized_official_domains
            else "incidental_list"
            if mention_count
            else "not_mentioned"
        ),
        "context_snippets": context_snippets(result.main_text, aliases),
    }


def diff_snapshots(previous_text: str, current_text: str) -> dict[str, Any]:
    similarity = SequenceMatcher(None, previous_text, current_text).ratio()
    return {
        "text_similarity": similarity,
        "material_change": similarity < 0.9,
        "change_summary": ("material_text_change" if similarity < 0.9 else "no_meaningful_change"),
    }
