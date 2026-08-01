from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog
from psycopg.types.json import Jsonb

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.scraping.fetcher import FetchResult, PageNotModified, fetch_page
from ai_visibility.scraping.snapshots import analyze_page_company, diff_snapshots
from ai_visibility.utils.urls import domain_from_url

logger = structlog.get_logger(__name__)


def _snapshot_structured_data(result: FetchResult) -> dict[str, Any]:
    extracted = result.structured_data
    payload = dict(extracted) if isinstance(extracted, dict) else {"extracted": extracted}
    payload["_fetch_metadata"] = result.fetch_metadata
    return payload


def _snapshot_extraction_usable(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    metadata = value.get("_fetch_metadata")
    if not isinstance(metadata, dict):
        return False
    quality = metadata.get("extraction_quality")
    return bool(isinstance(quality, dict) and quality.get("usable") is True)


def enqueue_report_pages(settings: Settings, report: dict[str, Any], *, limit: int = 25) -> int:
    client_id = report["company"]["client_id"]
    candidates = report.get("citation_intelligence", {}).get("material_comparison_url_deltas", [])
    candidates = sorted(
        candidates,
        key=lambda item: (
            item.get("status") not in {"new", "removed"},
            -abs(int(item.get("coverage_delta", 0))),
        ),
    )[:limit]
    queued = 0
    with connect(settings) as connection, connection.cursor() as cursor:
        for item in candidates:
            if item.get("status") == "removed":
                continue
            cursor.execute(
                """
                select id from public.ai_visibility_citation_pages
                where normalized_url = %s
                """,
                (item["url"],),
            )
            page = cursor.fetchone()
            if page is None:
                continue
            priority = (
                100
                if item.get("relationship") == "localized_new_owned_source"
                else 80
                if item.get("status") == "new"
                else 50 + min(30, abs(int(item.get("coverage_delta", 0))) * 5)
            )
            cursor.execute(
                """
                insert into public.ai_visibility_page_fetch_jobs(
                  page_id, client_id, priority, reason
                )
                values (%s, %s, %s, %s)
                on conflict (page_id, client_id, reason) do update set
                  priority = greatest(
                    ai_visibility_page_fetch_jobs.priority, excluded.priority
                  ),
                  status = case
                    when ai_visibility_page_fetch_jobs.status = 'complete'
                      then ai_visibility_page_fetch_jobs.status
                    else 'pending'
                  end,
                  updated_at = now()
                returning id
                """,
                (
                    page["id"],
                    client_id,
                    priority,
                    f"report:{item.get('status')}:{item.get('relationship')}",
                ),
            )
            cursor.fetchone()
            queued += 1
        connection.commit()
    return queued


def _claim_job(settings: Settings) -> dict[str, Any] | None:
    worker = socket.gethostname()
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            update public.ai_visibility_page_fetch_jobs
            set status = case when attempts >= 3 then 'failed' else 'retry' end,
                locked_at = null,
                locked_by = null,
                last_error = case
                  when attempts >= 3
                    then coalesce(last_error, 'Worker lease expired')
                  else last_error
                end,
                run_after = now(),
                updated_at = now()
            where status = 'processing'
              and locked_at < now() - (%s * interval '1 second')
            """,
            (settings.page_job_lease_seconds,),
        )
        cursor.execute(
            """
            with candidate as (
              select job.id
              from public.ai_visibility_page_fetch_jobs job
              where job.status in ('pending', 'retry')
                and job.run_after <= now()
              order by job.priority desc, job.created_at
              for update skip locked
              limit 1
            )
            update public.ai_visibility_page_fetch_jobs job
            set status = 'processing',
                attempts = attempts + 1,
                locked_at = now(),
                locked_by = %s,
                updated_at = now()
            from candidate
            where job.id = candidate.id
            returning job.*
            """,
            (worker,),
        )
        row = cursor.fetchone()
        connection.commit()
        if row is None:
            return None
        cursor.execute(
            """
            select page.normalized_url,
                   snapshot.etag,
                   snapshot.last_modified_header,
                   snapshot.fetch_method
            from public.ai_visibility_citation_pages page
            left join lateral (
              select etag, last_modified_header, fetch_method
              from public.ai_visibility_page_snapshots
              where page_id = page.id
              order by retrieved_at desc
              limit 1
            ) snapshot on true
            where page.id = %s
            """,
            (row["page_id"],),
        )
        page = cursor.fetchone()
        if page is None:
            raise RuntimeError("Claimed page job references a missing citation page.")
    return {**dict(row), **dict(page)}


def _persist_fetch(settings: Settings, job: dict[str, Any], result: FetchResult) -> None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            insert into public.ai_visibility_page_snapshots(
              page_id, http_status, final_url, content_type, title, main_text,
              main_text_hash, raw_content_hash, structured_data, fetch_method,
              etag, last_modified_header, robots_allowed
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
            on conflict (page_id, raw_content_hash) do update set
              retrieved_at = now(),
              http_status = excluded.http_status,
              final_url = excluded.final_url,
              title = excluded.title,
              main_text = excluded.main_text,
              main_text_hash = excluded.main_text_hash,
              structured_data = excluded.structured_data,
              fetch_method = excluded.fetch_method,
              etag = excluded.etag,
              last_modified_header = excluded.last_modified_header
            returning id
            """,
            (
                job["page_id"],
                result.status_code,
                result.final_url,
                result.content_type,
                result.title,
                result.main_text,
                result.main_text_hash,
                result.raw_content_hash,
                Jsonb(_snapshot_structured_data(result)),
                result.fetch_method,
                result.etag,
                result.last_modified_header,
            ),
        )
        snapshot_row = cursor.fetchone()
        if snapshot_row is None:
            raise RuntimeError("Page snapshot upsert did not return an ID.")
        snapshot_id = snapshot_row["id"]
        cursor.execute(
            """
            select company.id, company.canonical_name, company.official_domains
            from public.ai_visibility_client_companies client_company
            join public.ai_visibility_companies company
              on company.id = client_company.company_id
            where client_company.client_id = %s
            """,
            (job["client_id"],),
        )
        for company in cursor.fetchall():
            domains_value = company["official_domains"]
            domains = (
                [str(item) for item in domains_value] if isinstance(domains_value, list) else []
            )
            evidence = analyze_page_company(
                result,
                str(company["canonical_name"]),
                domains,
            )
            cursor.execute(
                """
                insert into public.ai_visibility_page_company_mentions(
                  snapshot_id, company_id, mention_count, mentioned_in_title,
                  mentioned_in_heading, links_to_official_domain,
                  publisher_is_company, mention_role, context_snippets, confidence
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (snapshot_id, company_id) do update set
                  mention_count = excluded.mention_count,
                  mentioned_in_title = excluded.mentioned_in_title,
                  mentioned_in_heading = excluded.mentioned_in_heading,
                  links_to_official_domain = excluded.links_to_official_domain,
                  publisher_is_company = excluded.publisher_is_company,
                  mention_role = excluded.mention_role,
                  context_snippets = excluded.context_snippets,
                  confidence = excluded.confidence,
                  updated_at = now()
                """,
                (
                    snapshot_id,
                    company["id"],
                    evidence["mention_count"],
                    evidence["mentioned_in_title"],
                    evidence["mentioned_in_heading"],
                    evidence["links_to_official_domain"],
                    evidence["publisher_is_company"],
                    evidence["mention_role"],
                    Jsonb(evidence["context_snippets"]),
                    1.0 if result.extraction_quality.get("usable") is True else 0.35,
                ),
            )
        cursor.execute(
            """
            select id, main_text, structured_data
            from public.ai_visibility_page_snapshots
            where page_id = %s and id <> %s
            order by retrieved_at desc
            limit 1
            """,
            (job["page_id"], snapshot_id),
        )
        previous = cursor.fetchone()
        if previous is not None:
            diff = diff_snapshots(
                str(previous["main_text"] or ""),
                result.main_text,
            )
            cursor.execute(
                """
                select company.canonical_name,
                       coalesce(previous_mention.mention_count, 0) as previous_count,
                       coalesce(current_mention.mention_count, 0) as current_count
                from public.ai_visibility_companies company
                left join public.ai_visibility_page_company_mentions previous_mention
                  on previous_mention.company_id = company.id
                 and previous_mention.snapshot_id = %s
                left join public.ai_visibility_page_company_mentions current_mention
                  on current_mention.company_id = company.id
                 and current_mention.snapshot_id = %s
                where previous_mention.company_id is not null
                   or current_mention.company_id is not null
                """,
                (previous["id"], snapshot_id),
            )
            extraction_quality_usable = (
                _snapshot_extraction_usable(previous["structured_data"])
                and result.extraction_quality.get("usable") is True
            )
            company_mention_changes = [
                {
                    "company": str(row["canonical_name"]),
                    "previous_count": int(row["previous_count"]),
                    "current_count": int(row["current_count"]),
                    "delta": int(row["current_count"]) - int(row["previous_count"]),
                }
                for row in cursor.fetchall()
                if extraction_quality_usable
                and int(row["current_count"]) != int(row["previous_count"])
            ]
            cursor.execute(
                """
                insert into public.ai_visibility_page_snapshot_diffs(
                  page_id, previous_snapshot_id, current_snapshot_id,
                  text_similarity, material_change, change_summary
                )
                values (%s, %s, %s, %s, %s, %s)
                on conflict (previous_snapshot_id, current_snapshot_id) do update set
                  text_similarity = excluded.text_similarity,
                  material_change = excluded.material_change,
                  change_summary = excluded.change_summary
                """,
                (
                    job["page_id"],
                    previous["id"],
                    snapshot_id,
                    diff["text_similarity"],
                    diff["material_change"],
                    Jsonb(
                        {
                            "classification": diff["change_summary"],
                            "extraction_quality_usable": extraction_quality_usable,
                            "company_mention_changes": company_mention_changes,
                        }
                    ),
                ),
            )
        cursor.execute(
            """
            update public.ai_visibility_page_fetch_jobs
            set status = 'complete', locked_at = null, locked_by = null,
                last_error = null, updated_at = now()
            where id = %s
            """,
            (job["id"],),
        )
        connection.commit()


def _complete_not_modified(settings: Settings, job: dict[str, Any]) -> None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            update public.ai_visibility_page_fetch_jobs
            set status = 'complete', locked_at = null, locked_by = null,
                last_error = null, updated_at = now()
            where id = %s
            """,
            (job["id"],),
        )
        connection.commit()


def _fail_job(settings: Settings, job: dict[str, Any], error: Exception) -> None:
    status = "failed" if int(job["attempts"]) >= 3 else "retry"
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            update public.ai_visibility_page_fetch_jobs
            set status = %s,
                run_after = now() + (
                  least(3600, power(2, attempts)::integer * 30) * interval '1 second'
                ),
                locked_at = null,
                locked_by = null,
                last_error = %s,
                updated_at = now()
            where id = %s
            """,
            (status, f"{type(error).__name__}: {error}"[:2000], job["id"]),
        )
        connection.commit()


def process_page_jobs(settings: Settings, *, limit: int = 20) -> dict[str, int]:
    if limit <= 0:
        return {"completed": 0, "failed_or_retried": 0}
    completed = 0
    failed = 0
    claimed = 0
    no_more_jobs = False
    claim_lock = threading.Lock()
    result_lock = threading.Lock()
    throttle_lock = threading.Lock()
    next_fetch_by_domain: dict[str, float] = {}

    def claim_next() -> dict[str, Any] | None:
        nonlocal claimed, no_more_jobs
        with claim_lock:
            if no_more_jobs or claimed >= limit:
                return None
            job = _claim_job(settings)
            if job is None:
                no_more_jobs = True
                return None
            claimed += 1
            return job

    def wait_for_domain(domain: str) -> None:
        now = time.monotonic()
        with throttle_lock:
            scheduled = max(now, next_fetch_by_domain.get(domain, now))
            next_fetch_by_domain[domain] = scheduled + settings.page_fetch_per_domain_delay_seconds
        remaining = scheduled - now
        if remaining > 0:
            time.sleep(remaining)

    def worker() -> None:
        nonlocal completed, failed
        while (job := claim_next()) is not None:
            job_completed = False
            job_failed = False
            try:
                domain = domain_from_url(str(job["normalized_url"]))
                wait_for_domain(domain)
                result = fetch_page(
                    settings,
                    str(job["normalized_url"]),
                    etag=(
                        str(job["etag"])
                        if job.get("etag") and job.get("fetch_method") != "browser_html"
                        else None
                    ),
                    last_modified_header=(
                        str(job["last_modified_header"])
                        if job.get("last_modified_header")
                        and job.get("fetch_method") != "browser_html"
                        else None
                    ),
                )
                _persist_fetch(settings, job, result)
                job_completed = True
                logger.info(
                    "page_job_completed",
                    job_id=str(job["id"]),
                    client_id=str(job["client_id"]),
                    page_url=job["normalized_url"],
                    fetch_method=result.fetch_method,
                    extraction_quality=result.extraction_quality.get("quality"),
                )
            except PageNotModified:
                _complete_not_modified(settings, job)
                job_completed = True
            except Exception as exc:
                _fail_job(settings, job, exc)
                job_failed = True
                logger.warning(
                    "page_job_failed",
                    job_id=str(job["id"]),
                    client_id=str(job["client_id"]),
                    error_type=type(exc).__name__,
                )
            with result_lock:
                completed += int(job_completed)
                failed += int(job_failed)

    worker_count = min(settings.page_fetch_max_workers, limit)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="page-fetch") as executor:
        futures = [executor.submit(worker) for _ in range(worker_count)]
        for future in futures:
            future.result()
    return {"completed": completed, "failed_or_retried": failed}


def list_failed_jobs(settings: Settings, *, limit: int = 100) -> list[dict[str, Any]]:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute(
            """
            select job.id, page.normalized_url, job.attempts, job.last_error,
                   job.updated_at
            from public.ai_visibility_page_fetch_jobs job
            join public.ai_visibility_citation_pages page on page.id = job.page_id
            where job.status = 'failed'
            order by job.updated_at desc
            limit %s
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def retry_failed_jobs(settings: Settings) -> int:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            update public.ai_visibility_page_fetch_jobs
            set status = 'retry', attempts = 0, run_after = now(),
                last_error = null, updated_at = now()
            where status = 'failed'
            """
        )
        count = cursor.rowcount
        connection.commit()
    return count
