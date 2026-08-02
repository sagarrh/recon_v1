from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from typing import Protocol

import openai
import structlog
from pydantic import Field

from aivc.config.settings import AivcSettings
from aivc.reporting.context import ReportInputSnapshot
from aivc.reporting.models import FinalReportSnapshot, StrictReportModel
from scout.config import get_config

logger = structlog.get_logger(__name__)


class TopicNarrative(StrictReportModel):
    cluster_id: str
    interpretation: str
    competitive_takeaway: str


class PriorityNarrative(StrictReportModel):
    priority_rank: int = Field(ge=1)
    what_is_happening: str
    why_it_matters: str
    working_hypothesis: str | None = None


class ClientNarrativeDraft(StrictReportModel):
    executive_narrative: str
    main_implication_title: str
    main_implication: str
    topic_narratives: list[TopicNarrative] = Field(default_factory=list)
    priority_narratives: list[PriorityNarrative] = Field(default_factory=list)
    leadership_decisions: list[str] = Field(default_factory=list)
    strategic_conclusion: str


def load_client_report_system_prompt() -> str:
    return (
        files("aivc.resources.prompts")
        .joinpath("client_report_system.md")
        .read_text(encoding="utf-8")
        .strip()
    )


def client_report_prompt_sha256() -> str:
    return hashlib.sha256(load_client_report_system_prompt().encode("utf-8")).hexdigest()


class NarrativeProvider(Protocol):
    def enrich(
        self,
        snapshot: FinalReportSnapshot,
        report_input: ReportInputSnapshot,
    ) -> FinalReportSnapshot: ...


class ReuseValidatedNarrative:
    """Retain the validated deterministic narrative when LLM use is disabled."""

    def enrich(
        self,
        snapshot: FinalReportSnapshot,
        report_input: ReportInputSnapshot,
    ) -> FinalReportSnapshot:
        snapshot.verify_checksum()
        report_input.verify_checksum()
        return snapshot


def _numeric_tokens(value: object) -> set[str]:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return {token.rstrip("%") for token in re.findall(r"\b\d+(?:\.\d+)?%?", text)}


def _validate_client_language(
    draft: ClientNarrativeDraft,
    report_input: ReportInputSnapshot,
) -> None:
    text = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False)
    lowered = text.casefold()
    found = sorted(
        set(
            re.findall(
                r"\b(?:proves|proven|caused|causes|drives|drove)\b|"
                r"\b(?:resulted in|because of)\b",
                lowered,
            )
        )
    )
    if found:
        raise ValueError("report narrative used unsupported causal language: " + ", ".join(found))
    long_decimals = sorted(set(re.findall(r"\b\d+\.\d{4,}\b", text)))
    if long_decimals:
        raise ValueError("report narrative exposed raw ratio decimals: " + ", ".join(long_decimals))
    if "total queries" in lowered or re.search(r"\bout of\b.{0,40}\bqueries\b", lowered):
        raise ValueError("report narrative mislabeled an answer denominator as queries")
    repeated_queries = [
        query.query
        for query in report_input.citation.queries
        if len(query.query) >= 20 and query.query.casefold() in lowered
    ]
    if repeated_queries:
        raise ValueError("report narrative reproduced a complete monitored question")


def _merge_draft(
    snapshot: FinalReportSnapshot,
    draft: ClientNarrativeDraft,
) -> FinalReportSnapshot:
    presentation = snapshot.client_presentation
    if presentation is None:
        raise ValueError("client presentation is unavailable")
    topic_drafts = {item.cluster_id: item for item in draft.topic_narratives}
    priority_drafts = {item.priority_rank: item for item in draft.priority_narratives}
    topics = []
    for topic in presentation.topics:
        topic_generated = topic_drafts.get(topic.cluster_id)
        topics.append(
            topic
            if topic_generated is None
            else topic.model_copy(
                update={
                    "interpretation": topic_generated.interpretation,
                    "competitive_takeaway": topic_generated.competitive_takeaway,
                }
            )
        )
    priorities = []
    for priority in presentation.priorities:
        priority_generated = priority_drafts.get(priority.priority_rank)
        priorities.append(
            priority
            if priority_generated is None
            else priority.model_copy(
                update={
                    "what_is_happening": priority_generated.what_is_happening,
                    "why_it_matters": priority_generated.why_it_matters,
                    "working_hypothesis": priority_generated.working_hypothesis,
                }
            )
        )
    enriched = presentation.model_copy(
        update={
            "executive_narrative": draft.executive_narrative,
            "main_implication_title": draft.main_implication_title,
            "main_implication": draft.main_implication,
            "topics": topics,
            "priorities": priorities,
            "leadership_decisions": draft.leadership_decisions,
            "strategic_conclusion": draft.strategic_conclusion,
        }
    )
    return snapshot.model_copy(update={"client_presentation": enriched}).sealed()


class StructuredLlmNarrative:
    """Generate prose only; measured fields and HTML remain deterministic."""

    def __init__(self, settings: AivcSettings) -> None:
        self.settings = settings

    def _generate(self, report_input: ReportInputSnapshot) -> ClientNarrativeDraft:
        scout = get_config()
        if not scout.openrouter_api_key.strip():
            raise RuntimeError("OPENROUTER_API_KEY is required for report narrative generation")
        model = self.settings.aivc_report_llm_model or scout.gemini_model
        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=scout.openrouter_api_key,
            timeout=scout.llm_timeout_seconds,
            max_retries=scout.llm_max_retries,
        )
        schema = ClientNarrativeDraft.model_json_schema()
        system_prompt = (
            f"{load_client_report_system_prompt()}\n\n"
            "Required output JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        user_prompt = (
            "Create the client report narrative from this exact evidence envelope:\n"
            + json.dumps(report_input.model_dump(mode="json"), ensure_ascii=False)
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = client.chat.completions.create(
                    model=model,
                    max_tokens=min(
                        self.settings.aivc_report_llm_max_tokens * (attempt + 1),
                        50_000,
                    ),
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                choice = response.choices[0]
                if getattr(choice, "finish_reason", None) == "length":
                    raise ValueError("report narrative response was truncated")
                content = choice.message.content or ""
                clean = content.strip()
                if clean.startswith("```json"):
                    clean = clean[7:]
                elif clean.startswith("```"):
                    clean = clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                payload = json.loads(clean.strip())
                if not isinstance(payload, dict):
                    raise ValueError("report narrative model returned a non-object JSON value")
                draft = ClientNarrativeDraft.model_validate(payload)
                allowed = _numeric_tokens(report_input.model_dump(mode="json"))
                introduced = _numeric_tokens(draft.model_dump(mode="json")) - allowed
                if introduced:
                    raise ValueError(
                        "report narrative introduced unsupported numeric values: "
                        + ", ".join(sorted(introduced))
                    )
                _validate_client_language(draft, report_input)
                return draft
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "client_report_narrative_retry",
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        raise RuntimeError("report narrative did not return valid structured JSON") from last_error

    def enrich(
        self,
        snapshot: FinalReportSnapshot,
        report_input: ReportInputSnapshot,
    ) -> FinalReportSnapshot:
        snapshot.verify_checksum()
        report_input.verify_checksum()
        try:
            enriched = _merge_draft(snapshot, self._generate(report_input))
            enriched.verify_checksum()
            return enriched
        except Exception as exc:
            if self.settings.aivc_report_llm_required:
                raise RuntimeError("Client report narrative generation failed") from exc
            logger.warning(
                "client_report_narrative_fallback",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            flags = sorted({*snapshot.data_quality_flags, "report_narrative_fallback"})
            limitations = [
                *snapshot.limitations,
                "The client narrative used the validated deterministic fallback.",
            ]
            fallback = snapshot.model_copy(
                update={"data_quality_flags": flags, "limitations": limitations}
            ).sealed()
            fallback.verify_checksum()
            return fallback
