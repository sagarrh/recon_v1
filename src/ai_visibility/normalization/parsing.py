from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def parse_json[T](value: Any, default: T) -> Any | T:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return value


def as_list(value: Any) -> list[Any]:
    parsed: Any = parse_json(value, [])
    return parsed if isinstance(parsed, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    parsed: Any = parse_json(value, {})
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
