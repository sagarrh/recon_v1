from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from aivc.reporting.models import FinalReportSnapshot


def validate_final_report_payload(payload: dict[str, Any]) -> FinalReportSnapshot:
    schema = json.loads(
        files("aivc.resources.schemas")
        .joinpath("final_report_snapshot.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)
    snapshot = FinalReportSnapshot.model_validate(payload)
    snapshot.verify_checksum()
    return snapshot


def validate_final_report_file(path: Path) -> FinalReportSnapshot:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("final report JSON must contain an object")
    return validate_final_report_payload(value)
