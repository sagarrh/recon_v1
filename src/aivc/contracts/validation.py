from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator


def validate_bundle_schema(payload: dict[str, Any]) -> None:
    resource = files("aivc.resources.schemas").joinpath("signal_bundle.schema.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)
