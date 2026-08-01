from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return normalize_space(value)


def normalize_query(value: str) -> str:
    return normalize_name(value)


def alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias.strip())
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE | re.UNICODE)


def literal_mentions(text: str, aliases: Iterable[str]) -> tuple[int, int | None]:
    matches: list[re.Match[str]] = []
    for alias in sorted({a.strip() for a in aliases if a.strip()}, key=len, reverse=True):
        matches.extend(alias_pattern(alias).finditer(text))
    if not matches:
        return 0, None
    selected: list[tuple[int, int]] = []
    candidates = sorted(
        {(match.start(), match.end()) for match in matches},
        key=lambda span: (-(span[1] - span[0]), span[0], span[1]),
    )
    for candidate in candidates:
        if any(candidate[0] < existing[1] and existing[0] < candidate[1] for existing in selected):
            continue
        selected.append(candidate)
    return len(selected), min(start for start, _ in selected)


def context_snippets(
    text: str, aliases: Iterable[str], *, radius: int = 100, limit: int = 3
) -> list[str]:
    snippets: list[str] = []
    for alias in sorted({a.strip() for a in aliases if a.strip()}, key=len, reverse=True):
        for match in alias_pattern(alias).finditer(text):
            start = max(0, match.start() - radius)
            end = min(len(text), match.end() + radius)
            snippet = normalize_space(text[start:end])
            if snippet not in snippets:
                snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets


def slugify(value: str) -> str:
    return normalize_name(value).replace(" ", "-") or "company"
