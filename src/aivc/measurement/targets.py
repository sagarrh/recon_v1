from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class PageTarget:
    host: str | None
    path: str


def normalize_query(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_page_target(value: str) -> PageTarget | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    candidate = (
        cleaned if "://" in cleaned else f"https://placeholder.invalid/{cleaned.lstrip('/')}"
    )
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").casefold() or None
    if host == "placeholder.invalid":
        host = None
    path = unquote(parsed.path or "/")
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return PageTarget(host=host, path=path)


def page_matches(value: str | None, targets: list[PageTarget]) -> bool:
    if not value:
        return False
    candidate = normalize_page_target(value)
    if candidate is None:
        return False
    for target in targets:
        if candidate.path != target.path:
            continue
        if candidate.host and target.host and candidate.host != target.host:
            continue
        return True
    return False


def normalized_page_targets(values: list[str]) -> list[PageTarget]:
    deduped: dict[tuple[str | None, str], PageTarget] = {}
    for value in values:
        target = normalize_page_target(value)
        if target is not None:
            deduped[(target.host, target.path)] = target
    return list(deduped.values())


def normalized_queries(values: list[str]) -> set[str]:
    return {normalized for value in values if (normalized := normalize_query(value))}
