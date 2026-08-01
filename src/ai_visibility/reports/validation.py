from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def schema_path() -> Traversable:
    """Return the packaged report schema for editable and wheel installs."""
    return files("ai_visibility.resources.schemas").joinpath(
        "company_intelligence_report.schema.json"
    )


def validate_report_schema(report: dict[str, Any]) -> None:
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(report), key=lambda item: list(item.absolute_path))
    if errors:
        details = "; ".join(
            f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors[:10]
        )
        raise ValueError(f"Report failed JSON Schema validation: {details}")
