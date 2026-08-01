from __future__ import annotations

from ai_visibility.utils.text import normalize_name

KNOWN_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "bdo": ("BDO", "BDO USA"),
    "ernst young": ("EY", "Ernst & Young", "Ernst and Young"),
    "pricewaterhousecoopers": ("PwC", "PricewaterhouseCoopers"),
    "redstone government consulting": (
        "Redstone GCI",
        "Redstone Government Consulting",
    ),
}

CANONICAL_DISPLAY_NAMES = {
    "bdo": "BDO",
    "ernst young": "Ernst & Young",
    "pricewaterhousecoopers": "PricewaterhouseCoopers",
    "redstone government consulting": "Redstone Government Consulting",
}


def aliases_for(company_name: str) -> list[str]:
    canonical = canonical_name(company_name)
    normalized = normalize_name(canonical)
    aliases = {canonical}
    for canonical, values in KNOWN_ALIAS_GROUPS.items():
        normalized_values = {normalize_name(item) for item in values}
        if normalized == canonical or normalized in normalized_values:
            aliases.update(values)
    return sorted(aliases, key=lambda item: (-len(item), item.casefold()))


def canonical_name(company_name: str) -> str:
    normalized = normalize_name(company_name)
    for canonical, values in KNOWN_ALIAS_GROUPS.items():
        if normalized == canonical or normalized in {normalize_name(item) for item in values}:
            return CANONICAL_DISPLAY_NAMES[canonical]
    return company_name.strip()
