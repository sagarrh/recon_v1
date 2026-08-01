from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ai_visibility.companies.aliases import aliases_for, canonical_name
from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.normalization.models import NormalizedRun, RawMonitoringRun
from ai_visibility.utils.text import context_snippets, literal_mentions, normalize_name


def list_client_ids(settings: Settings) -> list[UUID]:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute("select distinct client_id from public.ai_monitoring order by client_id")
        return [UUID(str(row["client_id"])) for row in cursor.fetchall()]


def load_raw_runs(settings: Settings, client_id: UUID) -> list[RawMonitoringRun]:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute(
            """
            select id, client_id, cluster_id, cluster_name, request_payload,
                   answers_list, citations_list, citations_data, companies_data, created_at
            from public.ai_monitoring
            where client_id = %s
            order by created_at, id
            """,
            (client_id,),
        )
        return [RawMonitoringRun.model_validate(dict(row)) for row in cursor.fetchall()]


def load_raw_run(settings: Settings, run_id: UUID) -> RawMonitoringRun:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute(
            """
            select id, client_id, cluster_id, cluster_name, request_payload,
                   answers_list, citations_list, citations_data, companies_data, created_at
            from public.ai_monitoring where id = %s
            """,
            (run_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise LookupError(f"Monitoring run {run_id} does not exist.")
    return RawMonitoringRun.model_validate(dict(row))


def persist_normalized_run(
    settings: Settings,
    run: NormalizedRun,
    *,
    client_company_name: str | None,
) -> None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            insert into public.ai_visibility_monitor_queries(
              query_key, client_id, cluster_id, cluster_name, base_query,
              normalized_base_query, service, method, configuration_hash,
              configuration_completeness
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (query_key) do update set
              cluster_id = excluded.cluster_id,
              cluster_name = excluded.cluster_name,
              base_query = excluded.base_query,
              configuration_completeness = excluded.configuration_completeness,
              updated_at = now()
            returning id
            """,
            (
                run.monitor_query_key,
                run.client_id,
                run.cluster_id,
                run.cluster_name,
                run.base_query,
                run.normalized_base_query,
                run.service,
                run.method,
                run.configuration_hash,
                run.configuration_completeness,
            ),
        )
        monitor_query_row = cursor.fetchone()
        if monitor_query_row is None:
            raise RuntimeError("Monitor-query upsert did not return an ID.")
        monitor_query_id = monitor_query_row["id"]
        cursor.execute(
            """
            insert into public.ai_visibility_run_processing(
              run_id, client_id, monitor_query_id, source_created_at, status,
              is_valid, invalid_reason, pipeline_version, data_quality_flags, normalized_at
            )
            values (%s, %s, %s, %s, 'normalized', %s, %s, %s, %s, now())
            on conflict (run_id) do update set
              monitor_query_id = excluded.monitor_query_id,
              status = excluded.status,
              is_valid = excluded.is_valid,
              invalid_reason = excluded.invalid_reason,
              pipeline_version = excluded.pipeline_version,
              data_quality_flags = excluded.data_quality_flags,
              normalized_at = now(),
              last_error = null,
              updated_at = now()
            """,
            (
                run.run_id,
                run.client_id,
                monitor_query_id,
                run.created_at,
                run.is_valid,
                run.invalid_reason,
                settings.pipeline_version,
                Jsonb(run.data_quality_flags),
            ),
        )
        company_ids: dict[str, UUID] = {}
        compact_client_name = (
            normalize_name(client_company_name).replace(" ", "") if client_company_name else ""
        )
        client_domains = {
            citation.domain.removeprefix("www.")
            for answer in run.answers
            for citation in answer.citations
            if compact_client_name
            and (
                citation.domain.removeprefix("www.") == f"{compact_client_name}.com"
                or citation.domain.removeprefix("www.").startswith(f"{compact_client_name}.")
            )
        }
        company_rows: list[tuple[str, str, Jsonb]] = []
        relationships: dict[str, str] = {}
        for _normalized, metric in run.company_metrics.items():
            relationship = (
                "other_tracked"
                if client_company_name is None
                else "client"
                if normalize_name(metric.company_name) == normalize_name(client_company_name)
                else "competitor"
            )
            canonical = canonical_name(metric.company_name)
            normalized_canonical = normalize_name(canonical)
            relationships[normalized_canonical] = relationship
            company_rows.append(
                (
                    canonical,
                    normalized_canonical,
                    Jsonb(sorted(client_domains) if relationship == "client" else []),
                )
            )
        if company_rows:
            cursor.executemany(
                """
                insert into public.ai_visibility_companies(
                  canonical_name, normalized_name, official_domains
                )
                values (%s, %s, %s)
                on conflict (normalized_name) do update
                  set canonical_name = excluded.canonical_name,
                      official_domains = (
                        select coalesce(jsonb_agg(distinct d.value), '[]'::jsonb)
                        from jsonb_array_elements_text(
                          ai_visibility_companies.official_domains
                          || excluded.official_domains
                        ) as d(value)
                      ),
                      updated_at = now()
                """,
                company_rows,
            )
            cursor.execute(
                """
                select id, normalized_name
                from public.ai_visibility_companies
                where normalized_name = any(%s)
                """,
                (list(relationships),),
            )
            company_ids = {
                str(row["normalized_name"]): UUID(str(row["id"])) for row in cursor.fetchall()
            }
            alias_rows: list[tuple[UUID, str, str, bool]] = []
            relationship_rows: list[tuple[UUID, UUID, str]] = []
            for normalized, metric in run.company_metrics.items():
                canonical = canonical_name(metric.company_name)
                normalized_canonical = normalize_name(canonical)
                company_id = company_ids[normalized_canonical]
                company_ids[normalized] = company_id
                relationship_rows.append(
                    (run.client_id, company_id, relationships[normalized_canonical])
                )
                alias_rows.extend(
                    (
                        company_id,
                        alias,
                        normalize_name(alias),
                        normalize_name(alias) == normalized_canonical,
                    )
                    for alias in aliases_for(canonical)
                )
            cursor.executemany(
                """
                insert into public.ai_visibility_company_aliases(
                  company_id, alias, normalized_alias, is_primary
                )
                values (%s, %s, %s, %s)
                on conflict (company_id, normalized_alias) do update
                  set alias = excluded.alias,
                      is_primary = excluded.is_primary
                """,
                alias_rows,
            )
            cursor.executemany(
                """
                insert into public.ai_visibility_client_companies(
                  client_id, company_id, relationship
                )
                values (%s, %s, %s)
                on conflict (client_id, company_id) do update
                  set relationship = excluded.relationship
                """,
                relationship_rows,
            )
        cursor.execute(
            """
            delete from public.ai_visibility_run_company_metrics
            where run_id = %s
            """,
            (run.run_id,),
        )
        cursor.execute(
            """
            delete from public.ai_visibility_answer_company_mentions
            where answer_id in (
              select id from public.ai_visibility_answers where run_id = %s
            )
            """,
            (run.run_id,),
        )
        cursor.execute(
            """
            delete from public.ai_visibility_answer_citations
            where answer_id in (
              select id from public.ai_visibility_answers where run_id = %s
            )
            """,
            (run.run_id,),
        )
        cursor.execute(
            """
            delete from public.ai_visibility_answers
            where run_id = %s
              and not (answer_number = any(%s))
            """,
            (run.run_id, [answer.answer_number for answer in run.answers]),
        )
        if run.answers:
            cursor.executemany(
                """
                insert into public.ai_visibility_answers(
                  run_id, answer_number, answer_text, answer_hash, word_count
                )
                values (%s, %s, %s, %s, %s)
                on conflict (run_id, answer_number) do update set
                  answer_text = excluded.answer_text,
                  answer_hash = excluded.answer_hash,
                  word_count = excluded.word_count
                """,
                [
                    (
                        run.run_id,
                        answer.answer_number,
                        answer.text,
                        answer.answer_hash,
                        answer.word_count,
                    )
                    for answer in run.answers
                ],
            )
            cursor.execute(
                """
                select id, answer_number
                from public.ai_visibility_answers
                where run_id = %s
                """,
                (run.run_id,),
            )
            answer_ids = {
                int(row["answer_number"]): UUID(str(row["id"])) for row in cursor.fetchall()
            }
        else:
            answer_ids = {}

        mention_rows: list[tuple[UUID, UUID, int, int | None, Jsonb]] = []
        page_domains: dict[str, str] = {}
        citation_rows_by_url: list[
            tuple[UUID, str, str, str | None, int, int | None, int | None, str]
        ] = []
        for answer in run.answers:
            answer_id = answer_ids[answer.answer_number]
            for normalized, metric in run.company_metrics.items():
                mention_count, first_position = literal_mentions(answer.text, metric.aliases)
                if mention_count:
                    mention_rows.append(
                        (
                            answer_id,
                            company_ids[normalized],
                            mention_count,
                            first_position,
                            Jsonb(context_snippets(answer.text, metric.aliases)),
                        )
                    )
            for citation in answer.citations:
                page_domains[citation.normalized_url] = citation.domain
                citation_rows_by_url.append(
                    (
                        answer_id,
                        citation.normalized_url,
                        citation.original_url,
                        citation.title,
                        citation.raw_occurrence_count,
                        citation.start_index,
                        citation.end_index,
                        citation.position_quality,
                    )
                )
        if mention_rows:
            cursor.executemany(
                """
                insert into public.ai_visibility_answer_company_mentions(
                  answer_id, company_id, literal_mention_count, first_position,
                  context_snippets
                )
                values (%s, %s, %s, %s, %s)
                on conflict (answer_id, company_id, detection_method) do update set
                  literal_mention_count = excluded.literal_mention_count,
                  first_position = excluded.first_position,
                  context_snippets = excluded.context_snippets,
                  updated_at = now()
                """,
                mention_rows,
            )
        if page_domains:
            cursor.executemany(
                """
                insert into public.ai_visibility_citation_pages(normalized_url, domain)
                values (%s, %s)
                on conflict (normalized_url) do update
                  set domain = excluded.domain, updated_at = now()
                """,
                list(page_domains.items()),
            )
            cursor.execute(
                """
                select id, normalized_url
                from public.ai_visibility_citation_pages
                where normalized_url = any(%s)
                """,
                (list(page_domains),),
            )
            page_ids = {
                str(row["normalized_url"]): UUID(str(row["id"])) for row in cursor.fetchall()
            }
            cursor.executemany(
                """
                insert into public.ai_visibility_answer_citations(
                  answer_id, page_id, original_url, title, raw_occurrence_count,
                  start_index, end_index, position_quality
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (answer_id, page_id) do update set
                  original_url = excluded.original_url,
                  title = excluded.title,
                  raw_occurrence_count = excluded.raw_occurrence_count,
                  start_index = excluded.start_index,
                  end_index = excluded.end_index,
                  position_quality = excluded.position_quality
                """,
                [
                    (
                        answer_id,
                        page_ids[normalized_url],
                        original_url,
                        title,
                        occurrence_count,
                        start_index,
                        end_index,
                        position_quality,
                    )
                    for (
                        answer_id,
                        normalized_url,
                        original_url,
                        title,
                        occurrence_count,
                        start_index,
                        end_index,
                        position_quality,
                    ) in citation_rows_by_url
                ],
            )
        if run.company_metrics:
            cursor.executemany(
                """
                insert into public.ai_visibility_run_company_metrics(
                  run_id, company_id, literal_answer_count, literal_answer_numbers,
                  literal_visibility, literal_total_mentions, upstream_count,
                  upstream_visibility, upstream_word_count, metric_difference,
                  data_quality_flags
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (run_id, company_id) do update set
                  literal_answer_count = excluded.literal_answer_count,
                  literal_answer_numbers = excluded.literal_answer_numbers,
                  literal_visibility = excluded.literal_visibility,
                  literal_total_mentions = excluded.literal_total_mentions,
                  upstream_count = excluded.upstream_count,
                  upstream_visibility = excluded.upstream_visibility,
                  upstream_word_count = excluded.upstream_word_count,
                  metric_difference = excluded.metric_difference,
                  data_quality_flags = excluded.data_quality_flags,
                  updated_at = now()
                """,
                [
                    (
                        run.run_id,
                        company_ids[normalized],
                        metric.literal_answer_count,
                        Jsonb(metric.literal_answer_numbers),
                        metric.literal_visibility,
                        metric.literal_total_mentions,
                        metric.upstream_count,
                        metric.upstream_visibility,
                        metric.upstream_word_count,
                        metric.metric_difference,
                        Jsonb(metric.data_quality_flags),
                    )
                    for normalized, metric in run.company_metrics.items()
                ],
            )
        connection.commit()


def processing_status(settings: Settings) -> dict[str, int]:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            select
              count(*) filter (where status = 'normalized') as normalized,
              count(*) filter (where is_valid) as valid,
              count(*) filter (where not is_valid) as invalid,
              count(*) filter (where status = 'failed') as failed
            from public.ai_visibility_run_processing
            """
        )
        row = cursor.fetchone() or {}
    return {key: int(value or 0) for key, value in row.items()}


def load_page_intelligence(
    settings: Settings, client_id: UUID, company_name: str
) -> list[dict[str, Any]]:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute(
            """
            select distinct on (page.id)
              page.normalized_url,
              page.domain,
              snapshot.id as snapshot_id,
              snapshot.retrieved_at,
              snapshot.title,
              snapshot.main_text_hash,
              snapshot.fetch_method,
              snapshot.structured_data -> '_fetch_metadata' -> 'extraction_quality'
                as extraction_quality,
              mention.mention_count,
              mention.mentioned_in_title,
              mention.mentioned_in_heading,
              mention.links_to_official_domain,
              mention.publisher_is_company,
              mention.mention_role,
              mention.confidence as page_mention_confidence,
              diff.text_similarity,
              diff.material_change,
              diff.change_summary,
              diff.previous_snapshot_at,
              diff.current_snapshot_at
            from public.ai_visibility_citation_pages page
            join public.ai_visibility_answer_citations citation
              on citation.page_id = page.id
            join public.ai_visibility_answers answer on answer.id = citation.answer_id
            join public.ai_visibility_run_processing run on run.run_id = answer.run_id
            left join public.ai_visibility_page_snapshots snapshot
              on snapshot.page_id = page.id
            left join public.ai_visibility_companies company
              on company.normalized_name = %s
            left join public.ai_visibility_page_company_mentions mention
              on mention.snapshot_id = snapshot.id and mention.company_id = company.id
            left join lateral (
              select snapshot_diff.text_similarity,
                     snapshot_diff.material_change,
                     snapshot_diff.change_summary,
                     previous_snapshot.retrieved_at as previous_snapshot_at,
                     current_snapshot.retrieved_at as current_snapshot_at
              from public.ai_visibility_page_snapshot_diffs snapshot_diff
              join public.ai_visibility_page_snapshots previous_snapshot
                on previous_snapshot.id = snapshot_diff.previous_snapshot_id
              join public.ai_visibility_page_snapshots current_snapshot
                on current_snapshot.id = snapshot_diff.current_snapshot_id
              where snapshot_diff.current_snapshot_id = snapshot.id
              order by snapshot_diff.created_at desc
              limit 1
            ) diff on true
            where run.client_id = %s and snapshot.id is not null
            order by page.id, snapshot.retrieved_at desc
            """,
            (normalize_name(company_name), client_id),
        )
        return [dict(row) for row in cursor.fetchall()]
