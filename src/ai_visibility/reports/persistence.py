from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ai_visibility.config.settings import Settings
from ai_visibility.database.connection import connect
from ai_visibility.reports.markdown import render_markdown
from ai_visibility.utils.hashing import stable_json_hash
from ai_visibility.utils.text import normalize_name, slugify


def write_report_files(
    settings: Settings, company_name: str, report: dict[str, Any]
) -> tuple[Path, Path]:
    directory = settings.report_output_dir / slugify(company_name)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "company-intelligence-report.json"
    markdown_path = directory / "company-intelligence-report.md"
    json_temp = json_path.with_suffix(".json.tmp")
    markdown_temp = markdown_path.with_suffix(".md.tmp")
    json_temp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_temp.write_text(render_markdown(report), encoding="utf-8")
    json_temp.replace(json_path)
    markdown_temp.replace(markdown_path)
    return json_path.resolve(), markdown_path.resolve()


def persist_report(settings: Settings, report: dict[str, Any]) -> UUID:
    company = report["company"]
    normalized_name = normalize_name(str(company["canonical_name"]))
    idempotency_key = stable_json_hash(
        {
            "client_id": company["client_id"],
            "company": normalized_name,
            "analysis_period": report["analysis_period"],
            "report_version": settings.pipeline_version,
        }
    )
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            select id from public.ai_visibility_companies where normalized_name = %s
            """,
            (normalized_name,),
        )
        company_row = cursor.fetchone()
        if company_row is None:
            raise RuntimeError("Client company was not persisted during normalization.")
        cursor.execute(
            """
            insert into public.ai_visibility_reports(
              idempotency_key, client_id, company_id, company_name,
              analysis_start, analysis_end, report_version, status,
              structured_report, generated_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, 'complete', %s, now())
            on conflict (idempotency_key) do update set
              status = 'complete',
              structured_report = excluded.structured_report,
              generated_at = now(),
              last_error = null,
              updated_at = now()
            returning id
            """,
            (
                idempotency_key,
                company["client_id"],
                company_row["id"],
                company["canonical_name"],
                report["analysis_period"].get("start"),
                report["analysis_period"].get("end"),
                settings.pipeline_version,
                Jsonb(report),
            ),
        )
        report_row = cursor.fetchone()
        if report_row is None:
            raise RuntimeError("Report upsert did not return an ID.")
        report_id = UUID(str(report_row["id"]))
        cursor.execute(
            """
            update public.ai_visibility_signals
            set status = 'inactive', updated_at = now()
            where client_id = %s and company_id = %s and status = 'active'
            """,
            (company["client_id"], company_row["id"]),
        )
        for signal in report.get("signals", []):
            observed = signal.get("observed", {})
            cursor.execute(
                """
                select monitor_query_id
                from public.ai_visibility_run_processing
                where run_id = %s
                """,
                (observed.get("current_run_id"),),
            )
            processing_row = cursor.fetchone()
            if processing_row is not None:
                warnings = signal.get("warnings", [])
                cursor.execute(
                    """
                    insert into public.ai_visibility_run_comparisons(
                      monitor_query_id, previous_run_id, current_run_id,
                      comparison_type, comparability_score, comparability_flags,
                      structured_delta
                    )
                    values (%s, %s, %s, 'adjacent_valid', %s, %s, %s)
                    on conflict (
                      previous_run_id, current_run_id, comparison_type
                    ) do update set
                      comparability_score = excluded.comparability_score,
                      comparability_flags = excluded.comparability_flags,
                      structured_delta = excluded.structured_delta
                    """,
                    (
                        processing_row["monitor_query_id"],
                        observed.get("previous_run_id"),
                        observed.get("current_run_id"),
                        0.75 if "execution_configuration_incomplete" in warnings else 1.0,
                        Jsonb(
                            [
                                warning
                                for warning in warnings
                                if warning == "execution_configuration_incomplete"
                            ]
                        ),
                        Jsonb(
                            {
                                "observed": observed,
                                "evidence": signal.get("evidence", {}),
                            }
                        ),
                    ),
                )
            signal_key = stable_json_hash(
                {
                    "client_id": company["client_id"],
                    "company": normalized_name,
                    "signal_type": signal.get("signal_type"),
                    "previous_run_id": observed.get("previous_run_id"),
                    "current_run_id": observed.get("current_run_id"),
                }
            )
            cursor.execute(
                """
                insert into public.ai_visibility_signals(
                  idempotency_key, client_id, company_id, signal_type,
                  severity, confidence, structured_payload, status
                )
                values (%s, %s, %s, %s, %s, %s, %s, 'active')
                on conflict (idempotency_key) do update set
                  severity = excluded.severity,
                  confidence = excluded.confidence,
                  structured_payload = excluded.structured_payload,
                  status = 'active',
                  updated_at = now()
                """,
                (
                    signal_key,
                    company["client_id"],
                    company_row["id"],
                    signal.get("signal_type", "unknown"),
                    abs(float(observed.get("literal_visibility_delta", 0.0))),
                    signal.get("confidence", "low"),
                    Jsonb(signal),
                ),
            )
        connection.commit()
    return report_id


def load_latest_persisted_report(settings: Settings, company_name: str) -> dict[str, Any] | None:
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("set transaction read only")
        cursor.execute(
            """
            select structured_report
            from public.ai_visibility_reports
            where lower(company_name) = lower(%s) and status = 'complete'
            order by generated_at desc nulls last, created_at desc
            limit 1
            """,
            (company_name,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    value = row["structured_report"]
    if isinstance(value, dict):
        return dict(value)
    parsed: Any = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("Persisted report is not a JSON object.")
    return dict(parsed)


def load_local_report(settings: Settings, company_name: str) -> dict[str, Any] | None:
    path = settings.report_output_dir / slugify(company_name) / "company-intelligence-report.json"
    if not path.exists():
        return None
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"Local report is not a JSON object: {path}")
    return dict(parsed)
