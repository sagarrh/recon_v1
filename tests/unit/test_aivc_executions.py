"""Execution records are the measurement anchor, so what they may contain is constrained.

Target pages recorded here scope GA4 revenue measurement. A page on a domain the client does not
own would attribute someone else's traffic to them, so it is refused rather than stored.
"""

import pytest

from aivc.database.executions import _validated_pages

OWNED = ("aprio.com",)


def test_owned_pages_are_canonicalized_like_the_recommendation_path():
    """One page must have one spelling, or the same page arrives twice and double-counts."""
    assert _validated_pages(["https://www.aprio.com/procurement/"], OWNED) == [
        "https://aprio.com/procurement"
    ]


def test_subdomains_of_an_owned_domain_are_accepted():
    assert _validated_pages(["https://blog.aprio.com/post"], OWNED) == [
        "https://blog.aprio.com/post"
    ]


def test_competitor_page_is_refused_loudly():
    with pytest.raises(ValueError) as exc:
        _validated_pages(["https://competitor.com/their-page"], OWNED)
    message = str(exc.value)
    assert "does not own" in message
    assert "competitor.com" in message
    assert "aprio.com" in message          # tells the operator what IS allowed


def test_refusal_is_loud_rather_than_a_silent_drop():
    """A typo must fail visibly; silently returning [] would look like a successful save."""
    with pytest.raises(ValueError):
        _validated_pages(["https://aprio.com/ok", "https://typo.com/x"], OWNED)


def test_lookalike_domain_is_not_owned():
    with pytest.raises(ValueError):
        _validated_pages(["https://notaprio.com/x"], OWNED)


def test_pages_across_several_owned_domains_are_all_accepted():
    accepted = _validated_pages(
        ["https://aprio.com/a", "https://aprio.co.uk/b"], ("aprio.com", "aprio.co.uk")
    )
    assert accepted == ["https://aprio.com/a", "https://aprio.co.uk/b"]


def test_client_with_no_registered_domain_can_record_no_pages():
    """Fail closed: with nothing to validate against, no page can be shown to be the client's."""
    with pytest.raises(ValueError) as exc:
        _validated_pages(["https://aprio.com/procurement"], ())
    assert "none registered" in str(exc.value)


def test_no_pages_is_always_fine():
    """Not every execution is page-level: ai_access and third_party actions have no target page."""
    assert _validated_pages([], OWNED) == []
    assert _validated_pages(None, OWNED) == []
    assert _validated_pages(None, ()) == []
