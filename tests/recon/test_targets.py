# test_targets.py — target validation: which pages a recommendation is allowed to claim.
# The load-bearing rule is that a target page must be on the CLIENT'S OWN domain. Everything else
# here exists to make that rule hard to erode.
from scout import targets as T
from scout.models.recommendation import Recommendation

CLIENT = {
    "client_id": "c1",
    "client_name": "Aprio",
    "company_domain": "aprio.com",
    "company_website": "https://www.aprio.com",
}


# ---------------------------------------------------------------------------
# Owned-domain derivation
# ---------------------------------------------------------------------------

def test_owned_domains_normalizes_both_operator_entered_fields():
    assert T.owned_domains(CLIENT) == frozenset({"aprio.com"})


def test_owned_domains_tolerates_messy_input():
    assert T.owned_domains({
        "company_domain": "  WWW.Aprio.com/  ",
        "company_website": "https://aprio.com:443/index.html",
    }) == frozenset({"aprio.com"})


def test_owned_domains_is_empty_when_client_has_none():
    assert T.owned_domains({}) == frozenset()


def test_subdomains_are_owned_but_lookalikes_are_not():
    owned = frozenset({"aprio.com"})
    assert T.is_owned("blog.aprio.com", owned)
    assert T.is_owned("www.aprio.com", owned)
    assert not T.is_owned("notaprio.com", owned)       # suffix match must not count
    assert not T.is_owned("aprio.com.evil.net", owned)
    assert not T.is_owned("", owned)


# ---------------------------------------------------------------------------
# Page validation — the third-party rejection rule
# ---------------------------------------------------------------------------

def test_competitor_and_third_party_pages_are_rejected():
    accepted, rejected = T.validate_target_pages([
        "https://www.aprio.com/procurement",
        "https://competitor.com/procurement",
        "https://forbes.com/some-article",
    ], CLIENT)
    assert accepted == ["https://aprio.com/procurement"]
    assert {r["url"] for r in rejected} == {
        "https://competitor.com/procurement", "https://forbes.com/some-article"
    }
    assert all(r["reason"] == "not_client_owned" for r in rejected)


def test_relative_paths_resolve_against_the_client_website():
    accepted, rejected = T.validate_target_pages(["/procurement"], CLIENT)
    assert accepted == ["https://aprio.com/procurement"]
    assert rejected == []


def test_relative_path_is_rejected_when_the_website_is_unknown():
    accepted, rejected = T.validate_target_pages(["/procurement"], {"company_domain": "aprio.com"})
    assert accepted == []
    assert rejected[0]["reason"] == "relative_path_without_known_client_website"


def test_pages_are_canonicalized_and_deduplicated():
    accepted, _ = T.validate_target_pages([
        "https://www.aprio.com/procurement/",
        "http://aprio.com/procurement?utm_source=ai#top",
        "aprio.com/procurement",
    ], CLIENT)
    assert accepted == ["https://aprio.com/procurement"]   # http variant collapses to one entry


def test_query_strings_are_dropped_so_the_ga4_join_can_match():
    accepted, _ = T.validate_target_pages(
        ["https://aprio.com/procurement?utm_campaign=x"], CLIENT
    )
    assert accepted == ["https://aprio.com/procurement"]


def test_non_http_schemes_are_rejected():
    _, rejected = T.validate_target_pages(["javascript:alert(1)", "ftp://aprio.com/x"], CLIENT)
    assert {r["reason"] for r in rejected} == {"not_client_owned", "unsupported_scheme"}


def test_page_list_is_capped():
    urls = [f"https://aprio.com/p{i}" for i in range(T.MAX_TARGET_PAGES + 3)]
    accepted, rejected = T.validate_target_pages(urls, CLIENT)
    assert len(accepted) == T.MAX_TARGET_PAGES
    assert all(r["reason"] == "over_target_page_cap" for r in rejected)


def test_a_client_with_no_domain_can_target_nothing():
    """Fail closed: with no way to verify ownership, no page is accepted."""
    accepted, rejected = T.validate_target_pages(
        ["https://aprio.com/procurement"], {"client_id": "c1"}
    )
    assert accepted == []
    assert rejected[0]["reason"] == "not_client_owned"


# ---------------------------------------------------------------------------
# Queries and action type
# ---------------------------------------------------------------------------

def test_queries_are_trimmed_collapsed_and_deduplicated():
    assert T.validate_target_queries([
        "  enterprise   procurement platform ", "Enterprise Procurement Platform", "", "   ",
    ]) == ["enterprise procurement platform"]


def test_unknown_action_type_falls_back_rather_than_failing():
    assert T.normalize_action_type("Content-Update") == "content_update"
    assert T.normalize_action_type("wishful thinking") == "other"
    assert T.normalize_action_type(None) == "other"


# ---------------------------------------------------------------------------
# Mapping confidence — how measurable the recommendation is
# ---------------------------------------------------------------------------

def test_mapping_confidence_grades_by_what_is_measurable():
    assert T.resolve_mapping_confidence(["https://aprio.com/x"], []) == T.MAPPING_EXACT
    assert T.resolve_mapping_confidence(["https://aprio.com/x"], ["q"]) == T.MAPPING_EXACT
    assert T.resolve_mapping_confidence([], ["q"]) == T.MAPPING_QUERY_ONLY
    assert T.resolve_mapping_confidence([], []) == T.MAPPING_UNMAPPED


def test_recommendation_claiming_only_third_party_pages_becomes_unmapped():
    """The end-to-end rule: a recommendation that targets only pages it does not own is not
    measurable, and must say so rather than silently keep the unowned page."""
    out = T.apply_targets({
        "target_pages": ["https://competitor.com/procurement"],
        "target_queries": [],
        "action_type": "content_update",
    }, CLIENT)
    assert out["target_pages"] == []
    assert out["mapping_confidence"] == T.MAPPING_UNMAPPED
    assert out["target_rejections"][0]["reason"] == "not_client_owned"


def test_apply_targets_produces_a_model_valid_shape():
    """Whatever apply_targets returns must satisfy the Recommendation field constraints."""
    out = T.apply_targets({
        "target_pages": ["https://aprio.com/procurement", "https://competitor.com/x"],
        "target_queries": ["enterprise procurement platform"],
        "action_type": "content_update",
    }, CLIENT)
    rec = Recommendation(
        investigation_id="i1", client_id="c1", client_name="Aprio", competitor_name="Rival",
        cluster_id="clu1", cluster_label="Procurement", shift_type="gain", type="defensive",
        priority="standard", probable_cause="a real probable cause worth reporting",
        confidence="medium", gap_analysis="a gap analysis long enough to pass validation",
        action_bullets=["do a", "do b", "do c"], summary="s", slack_report="sr",
        target_pages=out["target_pages"], target_queries=out["target_queries"],
        action_type=out["action_type"], mapping_confidence=out["mapping_confidence"],
        target_rejections=out["target_rejections"],
    )
    assert rec.mapping_confidence == "exact"
    assert rec.target_pages == ["https://aprio.com/procurement"]
