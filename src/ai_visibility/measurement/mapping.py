"""Match a recommendation's targets to rows the source can actually return.

An AI monitoring prompt is usually a sentence; a Google query is usually three words. Bridging that
gap by similarity is the tempting move and the wrong one — a fuzzy match that silently succeeds
attributes real traffic to a query the client never targeted, and nothing downstream can tell.

So matching here is exact or normalized-exact only. Anything weaker is surfaced as a *candidate*
for a human to approve and is never counted. `unmapped` is a legitimate, common result.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# Closed status set. Anything not on this list is a bug, not a new state.
EXACT = "exact"
NORMALIZED = "normalized_exact"
UNMAPPED = "unmapped"
REJECTED_CROSS_TENANT = "rejected_cross_tenant"


@dataclass(frozen=True)
class TargetMapping:
    """How one target resolved against what the source actually holds."""

    target: str
    status: str
    matched: tuple[str, ...] = ()
    # Near-misses, surfaced for human approval. Deliberately NOT counted in any metric.
    candidates: tuple[str, ...] = ()
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.status in (EXACT, NORMALIZED) and bool(self.matched)


@dataclass(frozen=True)
class MappingResult:
    queries: list[TargetMapping] = field(default_factory=list)
    pages: list[TargetMapping] = field(default_factory=list)

    @property
    def matched_queries(self) -> list[str]:
        return sorted({m for q in self.queries if q.usable for m in q.matched})

    @property
    def matched_pages(self) -> list[str]:
        return sorted({m for p in self.pages if p.usable for m in p.matched})

    @property
    def has_any_target(self) -> bool:
        return bool(self.matched_queries or self.matched_pages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": [
                {
                    "target": m.target, "status": m.status, "matched": list(m.matched),
                    "candidates": list(m.candidates), "reason": m.reason,
                }
                for m in self.queries
            ],
            "pages": [
                {
                    "target": m.target, "status": m.status, "matched": list(m.matched),
                    "candidates": list(m.candidates), "reason": m.reason,
                }
                for m in self.pages
            ],
            "matched_query_count": len(self.matched_queries),
            "matched_page_count": len(self.matched_pages),
        }


def normalize_query(value: str) -> str:
    """Casefold and collapse whitespace. Nothing else — no stemming, no token dropping."""
    return " ".join(str(value or "").split()).casefold()


def normalize_page(value: str) -> str:
    """Reduce a URL to a comparable (host, path) key.

    Host is retained deliberately: joining on path alone would merge two clients' /pricing pages on
    a shared property, which is exactly the cross-tenant leak this system must not produce."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    host = (parts.hostname or "").rstrip(".").removeprefix("www.")
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return f"{host}{path}" if host else ""


def _map_one(
    target: str,
    available: dict[str, list[str]],
    normalizer: Callable[[str], str],
    *,
    owned_check: Callable[[str], bool] | None = None,
) -> TargetMapping:
    key = normalizer(target)
    if not key:
        return TargetMapping(target=target, status=UNMAPPED, reason="empty after normalization")
    if owned_check is not None and not owned_check(target):
        return TargetMapping(
            target=target, status=REJECTED_CROSS_TENANT,
            reason="target is outside the client's own domains",
        )
    matched = available.get(key)
    if matched:
        return TargetMapping(
            target=target, status=NORMALIZED, matched=tuple(sorted(set(matched))),
            reason=f"matched {len(set(matched))} source value(s) on a normalized exact key",
        )
    # Near-misses are surfaced but never accepted: a substring match on a Google query is not
    # evidence that the client targeted it.
    candidates = tuple(sorted(
        original
        for available_key, originals in available.items()
        for original in originals
        if key in available_key or available_key in key
    )[:5])
    return TargetMapping(
        target=target, status=UNMAPPED, candidates=candidates,
        reason=(
            f"no exact match; {len(candidates)} near-miss candidate(s) require human approval"
            if candidates else "no match in the source for this window"
        ),
    )


def _index(values: Iterable[str], normalizer: Callable[[str], str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for value in values:
        key = normalizer(value)
        if key:
            index.setdefault(key, []).append(str(value))
    return index


def map_targets(
    *,
    target_queries: Iterable[str],
    target_pages: Iterable[str],
    source_queries: Iterable[str],
    source_pages: Iterable[str],
    owned_page_check: Callable[[str], bool] | None = None,
) -> MappingResult:
    """Resolve targets against the query and page values the source holds for this window."""
    query_index = _index(source_queries, normalize_query)
    page_index = _index(source_pages, normalize_page)
    return MappingResult(
        queries=[_map_one(q, query_index, normalize_query) for q in target_queries or []],
        pages=[
            _map_one(p, page_index, normalize_page, owned_check=owned_page_check)
            for p in target_pages or []
        ],
    )
