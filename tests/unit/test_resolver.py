from __future__ import annotations

from collections import Counter
from uuid import UUID

from ai_visibility.companies.resolver import _dominant_exact_client

PRIMARY = UUID("b88e87f3-0aa5-4da9-be48-2807b12d5a91")
ALTERNATE = UUID("7c06c100-2605-4ff2-aaad-9d0baedc78ca")


def test_unique_exact_client_is_selected() -> None:
    assert _dominant_exact_client(Counter({PRIMARY: 1})) == PRIMARY


def test_dominant_exact_history_is_selected() -> None:
    assert _dominant_exact_client(Counter({PRIMARY: 61, ALTERNATE: 1})) == PRIMARY


def test_small_or_tied_histories_remain_ambiguous() -> None:
    assert _dominant_exact_client(Counter({PRIMARY: 2, ALTERNATE: 1})) is None
    assert _dominant_exact_client(Counter({PRIMARY: 3, ALTERNATE: 3})) is None
