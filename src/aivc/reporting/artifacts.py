from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ai_visibility.utils.text import slugify
from aivc.reporting.context import ReportInputSnapshot
from aivc.reporting.models import ArtifactManifest, ArtifactRecord, FinalReportSnapshot


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _record(
    path: Path,
    artifact_type: Literal["citation_input", "recon_input", "report_input"],
) -> ArtifactRecord:
    content = path.read_bytes()
    return ArtifactRecord(
        artifact_type=artifact_type,
        path=str(path.resolve()),
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="application/json",
        generated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
    )


def _write_input_set(directory: Path, report_input: ReportInputSnapshot) -> dict[str, Path]:
    paths = {
        "citation_input": directory / "ai-citation-report-input.json",
        "recon_input": directory / "recon-report-input.json",
        "report_input": directory / "report-input-snapshot.json",
    }
    payloads = {
        "citation_input": report_input.citation.model_dump(mode="json"),
        "recon_input": report_input.recon.model_dump(mode="json"),
        "report_input": report_input.model_dump(mode="json"),
    }
    for name, payload in payloads.items():
        _atomic_json(paths[name], payload)
    return {name: path.resolve() for name, path in paths.items()}


def write_report_inputs(
    output_root: Path,
    snapshot: FinalReportSnapshot,
    report_input: ReportInputSnapshot,
) -> tuple[ArtifactManifest, dict[str, Path], dict[str, Path]]:
    """Write exact run-scoped inputs and refresh convenient latest input copies."""
    snapshot.verify_checksum()
    report_input.verify_checksum()
    slug = slugify(snapshot.client.canonical_name)
    run_root = output_root / slug / "runs" / str(snapshot.parent_run_id)
    run_paths = _write_input_set(run_root / "inputs", report_input)
    latest_paths = _write_input_set(output_root / slug / "inputs", report_input)
    manifest = ArtifactManifest(
        report_id=snapshot.report_id,
        parent_run_id=snapshot.parent_run_id,
        snapshot_checksum=str(snapshot.checksum),
        artifacts=[
            _record(run_paths["citation_input"], "citation_input"),
            _record(run_paths["recon_input"], "recon_input"),
            _record(run_paths["report_input"], "report_input"),
        ],
    )
    manifest_path = run_root / "input-manifest.json"
    _atomic_json(manifest_path, manifest.model_dump(mode="json"))
    run_paths["manifest"] = manifest_path.resolve()
    return manifest, run_paths, latest_paths
