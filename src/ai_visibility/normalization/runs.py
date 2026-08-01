from __future__ import annotations

from collections import Counter
from typing import Any

from ai_visibility.companies.aliases import aliases_for, canonical_name
from ai_visibility.normalization.models import (
    CompanyMetric,
    NormalizedAnswer,
    NormalizedCitation,
    NormalizedRun,
    RawMonitoringRun,
)
from ai_visibility.normalization.parsing import as_dict, as_list, number_or_none
from ai_visibility.utils.hashing import sha256_text, stable_json_hash
from ai_visibility.utils.text import (
    context_snippets,
    literal_mentions,
    normalize_name,
    normalize_query,
)
from ai_visibility.utils.urls import domain_from_url, normalize_url

_EXECUTION_FIELDS = {
    "model",
    "model_version",
    "prompt_version",
    "retrieval_configuration",
    "language",
    "geography",
    "generation_settings",
}


def _configuration(payload: dict[str, Any]) -> tuple[str, str]:
    controls = {key: payload[key] for key in sorted(_EXECUTION_FIELDS) if key in payload}
    completeness = "complete" if _EXECUTION_FIELDS.issubset(payload) else "incomplete"
    # An empty controls object intentionally groups otherwise identical legacy runs.
    return stable_json_hash(controls), completeness


def _citation_values(item: Any) -> tuple[str | None, str | None, int | None, int | None]:
    data = as_dict(item)
    nested = as_dict(data.get("citation"))
    if nested:
        data = {**nested, **data}
    url = data.get("url") or data.get("uri") or data.get("link")
    title = data.get("title")
    start = data.get("start_index", data.get("startIndex"))
    end = data.get("end_index", data.get("endIndex"))
    try:
        start_value = int(start) if start is not None else None
        end_value = int(end) if end is not None else None
    except (TypeError, ValueError):
        start_value, end_value = None, None
    return (
        str(url) if url else None,
        str(title) if title else None,
        start_value,
        end_value,
    )


def _normalize_citations(group: Any, answer_number: int) -> list[NormalizedCitation]:
    items = as_list(group)
    occurrences: Counter[str] = Counter()
    details: dict[str, tuple[str, str, str | None, int | None, int | None, str]] = {}
    for item in items:
        url, title, start, end = _citation_values(item)
        if not url:
            continue
        try:
            normalized = normalize_url(url)
        except ValueError:
            continue
        if start == 0 and end == 0:
            quality = "zeroed"
        elif start is not None and end is not None and start >= 0 and end > start:
            quality = "valid"
        else:
            quality = "unavailable"
        occurrences[normalized] += 1
        if normalized not in details:
            details[normalized] = (
                url,
                domain_from_url(normalized),
                title,
                start,
                end,
                quality,
            )
        elif quality == "zeroed":
            old = details[normalized]
            details[normalized] = (*old[:5], "zeroed")
    return [
        NormalizedCitation(
            answer_number=answer_number,
            original_url=details[url][0],
            normalized_url=url,
            domain=details[url][1],
            title=details[url][2],
            raw_occurrence_count=count,
            start_index=details[url][3],
            end_index=details[url][4],
            position_quality=details[url][5],
        )
        for url, count in sorted(occurrences.items())
    ]


def _upstream_metrics(companies_data: dict[str, Any], company_name: str) -> dict[str, Any]:
    target = normalize_name(canonical_name(company_name))
    candidate_aliases = {normalize_name(alias) for alias in aliases_for(company_name)}
    candidates: list[tuple[bool, float, dict[str, Any]]] = []
    for name, metrics in companies_data.items():
        normalized = normalize_name(name)
        if normalized == target or normalized in candidate_aliases:
            parsed = as_dict(metrics)
            candidates.append(
                (
                    normalized == target,
                    number_or_none(parsed.get("count")) or 0.0,
                    parsed,
                )
            )
    if not candidates:
        return {}
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def normalize_run(raw: RawMonitoringRun, company_names: set[str]) -> NormalizedRun:
    payload = as_dict(raw.request_payload)
    answers_raw = as_list(raw.answers_list)
    citation_groups = as_list(raw.citations_list)
    companies_data = as_dict(raw.companies_data)

    base_query = str(payload.get("base_query") or payload.get("query") or "").strip()
    service = str(payload.get("service") or "unknown").strip().casefold()
    method = str(payload.get("method") or "unknown").strip().casefold()
    configuration_hash, completeness = _configuration(payload)
    query_identity = {
        "client_id": str(raw.client_id),
        "base_query": normalize_query(base_query),
        "service": service,
        "method": method,
        "configuration_hash": configuration_hash,
    }

    answers: list[NormalizedAnswer] = []
    for index, value in enumerate(answers_raw, start=1):
        if isinstance(value, dict):
            text = str(value.get("answer") or value.get("text") or value.get("content") or "")
        else:
            text = str(value)
        group = citation_groups[index - 1] if index <= len(citation_groups) else []
        answers.append(
            NormalizedAnswer(
                answer_number=index,
                text=text,
                answer_hash=sha256_text(text),
                word_count=len(text.split()),
                citations=_normalize_citations(group, index),
            )
        )

    is_valid = bool(answers)
    invalid_reason = None if is_valid else "zero_answers"
    flags: list[str] = []
    if completeness == "incomplete":
        flags.append("execution_configuration_incomplete")
    if any(
        citation.position_quality in {"zeroed", "unavailable"}
        for answer in answers
        for citation in answer.citations
    ):
        flags.append("citation_positions_unavailable")

    all_company_names = {canonical_name(str(name)) for name in companies_data} | {
        canonical_name(name) for name in company_names
    }
    metrics: dict[str, CompanyMetric] = {}
    for company_name in sorted(all_company_names, key=str.casefold):
        aliases = aliases_for(company_name)
        answer_numbers: list[int] = []
        total_mentions = 0
        for answer in answers:
            count, _ = literal_mentions(answer.text, aliases)
            if count:
                answer_numbers.append(answer.answer_number)
                total_mentions += count
                # Calculate snippets here so mention extraction remains deterministic.
                context_snippets(answer.text, aliases)
        upstream = _upstream_metrics(companies_data, company_name)
        upstream_count = number_or_none(upstream.get("count"))
        upstream_visibility = number_or_none(upstream.get("visibility"))
        upstream_word_count = number_or_none(
            upstream.get("word_count", upstream.get("total_word_count"))
        )
        difference = (
            float(len(answer_numbers)) - upstream_count if upstream_count is not None else None
        )
        metric_flags: list[str] = []
        if difference is not None and abs(difference) > 1e-9:
            metric_flags.append("company_metric_mismatch")
        canonical_company_name = canonical_name(company_name)
        metrics[normalize_name(canonical_company_name)] = CompanyMetric(
            company_name=canonical_company_name,
            aliases=aliases,
            literal_answer_count=len(answer_numbers),
            literal_answer_numbers=answer_numbers,
            literal_visibility=len(answer_numbers) / len(answers) if answers else 0.0,
            literal_total_mentions=total_mentions,
            upstream_count=upstream_count,
            upstream_visibility=upstream_visibility,
            upstream_word_count=upstream_word_count,
            metric_difference=difference,
            data_quality_flags=metric_flags,
        )

    if any("company_metric_mismatch" in metric.data_quality_flags for metric in metrics.values()):
        flags.append("company_metric_mismatch")
    if not is_valid:
        flags.append("failed_monitoring_run")

    return NormalizedRun(
        run_id=raw.id,
        client_id=raw.client_id,
        created_at=raw.created_at,
        cluster_id=raw.cluster_id,
        cluster_name=raw.cluster_name,
        base_query=base_query,
        normalized_base_query=normalize_query(base_query),
        service=service,
        method=method,
        configuration_hash=configuration_hash,
        configuration_completeness=completeness,
        monitor_query_key=stable_json_hash(query_identity),
        is_valid=is_valid,
        invalid_reason=invalid_reason,
        answers=answers,
        company_metrics=metrics,
        data_quality_flags=sorted(set(flags)),
    )
