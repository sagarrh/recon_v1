from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

import structlog

from ai_visibility.companies.aliases import aliases_for, canonical_name
from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.normalization.parsing import as_dict, as_list
from ai_visibility.utils.text import literal_mentions, normalize_name

logger = structlog.get_logger(__name__)

_MIN_DOMINANT_EXACT_RUNS = 3
_MIN_DOMINANCE_RATIO = 3


@dataclass(frozen=True)
class ResolvedClient:
    canonical_name: str
    client_id: UUID
    aliases: tuple[str, ...]
    official_domains: tuple[str, ...] = ()


def _dominant_exact_client(exact_counts: Counter[UUID]) -> UUID | None:
    """Return a uniquely dominant exact-match history, otherwise remain ambiguous."""
    ranked = sorted(exact_counts.items(), key=lambda item: (-item[1], str(item[0])))
    if not ranked:
        return None
    if len(ranked) == 1:
        return ranked[0][0]
    (top_id, top_count), (_, runner_up_count) = ranked[:2]
    if (
        top_count >= _MIN_DOMINANT_EXACT_RUNS
        and top_count >= runner_up_count * _MIN_DOMINANCE_RATIO
    ):
        return top_id
    return None


def resolve_client(settings: Settings, company_name: str) -> ResolvedClient:
    canonical = canonical_name(company_name)
    normalized = normalize_name(canonical)
    if not normalized:
        raise ValueError("Company name must not be empty.")
    aliases = aliases_for(canonical)
    normalized_aliases = {normalize_name(alias) for alias in aliases}
    alternation = "|".join(re.escape(alias) for alias in aliases)
    company_key_pattern = rf'"(?:{alternation})"\s*:'
    answer_pattern = rf"(^|[^[:alnum:]_])(?:{alternation})([^[:alnum:]_]|$)"
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute(
            """
            select to_regclass('public.ai_visibility_client_companies') as relation
            """
        )
        registry_exists = (cursor.fetchone() or {}).get("relation") is not None
        if registry_exists:
            cursor.execute(
                """
                select distinct client_company.client_id, company.official_domains
                from public.ai_visibility_client_companies client_company
                join public.ai_visibility_companies company
                  on company.id = client_company.company_id
                left join public.ai_visibility_company_aliases alias
                  on alias.company_id = company.id
                where client_company.relationship = 'client'
                  and (
                    company.normalized_name = any(%s)
                    or alias.normalized_alias = any(%s)
                  )
                order by client_company.client_id
                """,
                (list(normalized_aliases), list(normalized_aliases)),
            )
            registered_rows = cursor.fetchall()
            registered = [UUID(str(row["client_id"])) for row in registered_rows]
            if len(registered) == 1:
                domains_value = registered_rows[0]["official_domains"]
                return ResolvedClient(
                    canonical_name=canonical,
                    client_id=registered[0],
                    aliases=tuple(aliases),
                    official_domains=tuple(str(domain) for domain in domains_value)
                    if isinstance(domains_value, list)
                    else (),
                )
            if len(registered) > 1:
                raise LookupError(
                    f"Company {company_name!r} is registered as a client for "
                    f"{len(registered)} client IDs."
                )
        cursor.execute(
            """
            select am.client_id, am.companies_data, am.answers_list
            from public.ai_monitoring am
            where am.companies_data::text ~* %s
               or am.answers_list::text ~* %s
            order by am.client_id
            """,
            (company_key_pattern, answer_pattern),
        )
        exact_counts: Counter[UUID] = Counter()
        answer_clients: set[UUID] = set()
        for row in cursor.fetchall():
            client_id = UUID(str(row["client_id"]))
            company_keys = {normalize_name(str(name)) for name in as_dict(row["companies_data"])}
            if company_keys & normalized_aliases:
                exact_counts[client_id] += 1
                continue
            for answer in as_list(row["answers_list"]):
                text = (
                    str(answer.get("answer") or answer.get("text") or "")
                    if isinstance(answer, dict)
                    else str(answer)
                )
                if literal_mentions(text, aliases)[0]:
                    answer_clients.add(client_id)
                    break
    dominant_client = _dominant_exact_client(exact_counts)
    if dominant_client is not None:
        client_ids = [dominant_client]
        if len(exact_counts) > 1:
            logger.info(
                "client_resolved_from_dominant_exact_history",
                company=canonical,
                client_id=str(dominant_client),
                exact_run_count=exact_counts[dominant_client],
                alternate_client_count=len(exact_counts) - 1,
            )
    else:
        client_ids = sorted(exact_counts or Counter(answer_clients), key=str)
    if not client_ids:
        raise LookupError(
            f"No client monitoring history could be resolved for company {company_name!r}."
        )
    if len(client_ids) > 1:
        raise LookupError(
            f"Company {company_name!r} is ambiguous across {len(client_ids)} clients."
        )
    return ResolvedClient(
        canonical_name=canonical,
        client_id=client_ids[0],
        aliases=tuple(aliases),
    )
