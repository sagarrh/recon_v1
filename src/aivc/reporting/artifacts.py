from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ai_visibility.utils.text import slugify
from aivc.reporting.context import ReportInputSnapshot
from aivc.reporting.models import ArtifactManifest, ArtifactRecord, FinalReportSnapshot
from aivc.reporting.renderers import render_html, render_json, render_markdown


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _record(
    path: Path,
    artifact_type: Literal[
        "json",
        "markdown",
        "html",
        "manifest",
        "citation_input",
        "recon_input",
        "report_input",
        "report_content",
    ],
    mime_type: str,
) -> ArtifactRecord:
    content = path.read_bytes()
    return ArtifactRecord(
        artifact_type=artifact_type,
        path=str(path.resolve()),
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type=mime_type,
        generated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
    )


def write_final_report_artifacts(
    output_root: Path,
    snapshot: FinalReportSnapshot,
    *,
    report_input: ReportInputSnapshot | None = None,
    write_latest_copies: bool = False,
) -> tuple[ArtifactManifest, dict[str, Path]]:
    snapshot.verify_checksum()
    if report_input is not None:
        report_input.verify_checksum()
    slug = slugify(snapshot.client.canonical_name)
    run_dir = output_root / slug / "runs" / str(snapshot.parent_run_id)
    paths = {
        "json": run_dir / "final-report.json",
        "markdown": run_dir / "final-report.md",
        "html": run_dir / "final-report.html",
        "report_content": run_dir / "final-report-content.json",
        "manifest": run_dir / "artifact-manifest.json",
    }
    rendered = {
        "json": render_json(snapshot),
        "markdown": render_markdown(snapshot),
        "html": render_html(snapshot),
    }
    for name, content in rendered.items():
        _atomic_write(paths[name], content)

    presentation = snapshot.client_presentation
    if presentation is None:
        raise ValueError("client report content is missing")
    _atomic_write(
        paths["report_content"],
        json.dumps(presentation.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )

    if report_input is not None:
        input_dir = run_dir / "inputs"
        paths.update(
            {
                "citation_input": input_dir / "ai-citation-report-input.json",
                "recon_input": input_dir / "recon-report-input.json",
                "report_input": input_dir / "report-input-snapshot.json",
            }
        )
        input_payloads = {
            "citation_input": report_input.citation.model_dump(mode="json"),
            "recon_input": report_input.recon.model_dump(mode="json"),
            "report_input": report_input.model_dump(mode="json"),
        }
        for name, payload in input_payloads.items():
            _atomic_write(
                paths[name],
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            )

    records = [
        _record(paths["json"], "json", "application/json"),
        _record(paths["markdown"], "markdown", "text/markdown; charset=utf-8"),
        _record(paths["html"], "html", "text/html; charset=utf-8"),
        _record(paths["report_content"], "report_content", "application/json"),
    ]
    if report_input is not None:
        records.extend(
            [
                _record(paths["citation_input"], "citation_input", "application/json"),
                _record(paths["recon_input"], "recon_input", "application/json"),
                _record(paths["report_input"], "report_input", "application/json"),
            ]
        )
    manifest = ArtifactManifest(
        report_id=snapshot.report_id,
        parent_run_id=snapshot.parent_run_id,
        report_profile=snapshot.config.report_profile,
        report_audience=snapshot.config.report_audience,
        snapshot_checksum=str(snapshot.checksum),
        artifacts=records,
    )
    _atomic_write(
        paths["manifest"],
        json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )

    if write_latest_copies:
        latest_dir = output_root / slug
        for name, content in rendered.items():
            extension = "md" if name == "markdown" else name
            _atomic_write(latest_dir / f"final-report.{extension}", content)
    return manifest, {name: path.resolve() for name, path in paths.items()}


def refresh_latest_artifacts(
    output_root: Path,
    snapshot: FinalReportSnapshot,
    report_input: ReportInputSnapshot | None = None,
) -> dict[str, Path]:
    """Refresh convenient latest copies after database persistence succeeds."""
    snapshot.verify_checksum()
    if report_input is not None:
        report_input.verify_checksum()
    latest_dir = output_root / slugify(snapshot.client.canonical_name)
    rendered = {
        "json": render_json(snapshot),
        "md": render_markdown(snapshot),
        "html": render_html(snapshot),
    }
    paths: dict[str, Path] = {}
    for extension, content in rendered.items():
        path = latest_dir / f"final-report.{extension}"
        _atomic_write(path, content)
        paths[extension] = path.resolve()
    presentation = snapshot.client_presentation
    if presentation is None:
        raise ValueError("client report content is missing")
    content_path = latest_dir / "final-report-content.json"
    _atomic_write(
        content_path,
        json.dumps(presentation.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    paths["report_content"] = content_path.resolve()
    if report_input is not None:
        input_dir = latest_dir / "inputs"
        input_payloads = {
            "citation_input": (
                input_dir / "ai-citation-report-input.json",
                report_input.citation.model_dump(mode="json"),
            ),
            "recon_input": (
                input_dir / "recon-report-input.json",
                report_input.recon.model_dump(mode="json"),
            ),
            "report_input": (
                input_dir / "report-input-snapshot.json",
                report_input.model_dump(mode="json"),
            ),
        }
        for name, (path, payload) in input_payloads.items():
            _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            paths[name] = path.resolve()
    return paths
