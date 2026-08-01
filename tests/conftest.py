from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import pytest

from ai_visibility.normalization.models import RawMonitoringRun

CLIENT_ID = UUID("b88e87f3-0aa5-4da9-be48-2807b12d5a91")
PREVIOUS_ID = UUID("fb279a6c-f285-4c8a-afce-78469c9455e1")
CURRENT_ID = UUID("1ca57705-51b6-47a6-9725-8d43dbb0a937")
QUERY = "Who can help me prepare a certificate of cost and pricing data for a federal contract bid?"
MAIN_URL = (
    "https://www.aprio.com/insights-events/"
    "when-does-cost-or-pricing-data-need-to-be-certified-and-what-does-that-mean/"
)
NEW_URL = (
    "https://www.aprio.com/insights-events/"
    "how-to-account-for-ai-and-token-costs-on-government-contracts-"
    "a-far-dfars-and-cas-compliance-guide-ins-article-gc/"
)
BUNDLE = [
    "Cherry Bekaert",
    "Aprio",
    "CohnReznick",
    "BDO",
    "Baker Tilly",
    "Bennett Thrasher",
]


def _citation(url: str) -> dict[str, Any]:
    return {"url": url, "start_index": 0, "end_index": 0}


@pytest.fixture
def raw_aprio_pair() -> tuple[RawMonitoringRun, RawMonitoringRun]:
    previous_answers = [
        f"Federal cost and pricing guidance response {index}." for index in range(1, 21)
    ]
    current_answers = [
        (
            f"Federal guidance response {index}."
            if index < 8
            else f"Recommended firms include {', '.join(BUNDLE)}. Response {index}."
        )
        for index in range(1, 22)
    ]
    previous_citations: list[list[dict[str, Any]]] = []
    for index in range(1, 21):
        group: list[dict[str, Any]] = []
        if index <= 17:
            group.extend([_citation(MAIN_URL)] * (2 if index <= 7 else 1))
        previous_citations.append(group)
    current_citations: list[list[dict[str, Any]]] = []
    for index in range(1, 22):
        group = [_citation(MAIN_URL)] * (5 if index <= 3 else 4)
        if index == 14:
            group.append(_citation(NEW_URL))
        current_citations.append(group)
    company_names = [*BUNDLE, "Eubanks Accounting & Advisory", "HKA"]
    previous_companies = {name: {"count": 0, "visibility": 0.0} for name in company_names}
    previous_companies.update(
        {
            "Cherry Bekaert": {"count": 2, "visibility": 0.1},
            "CohnReznick": {"count": 8, "visibility": 0.4},
            "Baker Tilly": {"count": 7, "visibility": 0.35},
            "Eubanks Accounting & Advisory": {"count": 7, "visibility": 0.35},
            "HKA": {"count": 4, "visibility": 0.2},
        }
    )
    current_companies = {name: {"count": 14, "visibility": 14 / 21} for name in BUNDLE}
    current_companies["Aprio"] = {"count": 10, "visibility": 10 / 21}
    current_companies["BDO"] = {"count": 8, "visibility": 8 / 21}
    current_companies["Baker Tilly"] = {"count": 12, "visibility": 12 / 21}
    current_companies["Eubanks Accounting & Advisory"] = {
        "count": 0,
        "visibility": 0.0,
    }
    current_companies["HKA"] = {"count": 0, "visibility": 0.0}
    payload = {"service": "gemini", "method": "moe", "base_query": QUERY}
    previous = RawMonitoringRun(
        id=PREVIOUS_ID,
        client_id=CLIENT_ID,
        cluster_id="govcon",
        cluster_name="Government Contracting",
        request_payload={**payload, "user_data": {"task_id": "old"}},
        answers_list=previous_answers,
        citations_list=previous_citations,
        citations_data={},
        companies_data=previous_companies,
        created_at=datetime.fromisoformat("2026-06-09T20:50:11.141433+00:00"),
    )
    current = RawMonitoringRun(
        id=CURRENT_ID,
        client_id=CLIENT_ID,
        cluster_id="govcon",
        cluster_name="Government Contracting",
        request_payload={**payload, "user_data": {"task_id": "new"}},
        answers_list=current_answers,
        citations_list=current_citations,
        citations_data={},
        companies_data=current_companies,
        created_at=datetime.fromisoformat("2026-07-11T20:54:12.981520+00:00"),
    )
    return previous, current
