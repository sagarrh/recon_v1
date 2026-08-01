# schema_gen.py — Tier 3: deterministic JSON-LD templates for GEO_RECOMMENDED_SCHEMA_TYPES (no LLM).
# Missing facts are OMITTED, never fabricated. Review/AggregateRating REFUSE without grounded
# rating facts (fabricated review markup = honesty + Google-policy violation, D12).
import json

from scout.utils import ensure_scheme as _url


def _org_core(facts: dict, at_type: str) -> dict:
    out: dict = {"@context": "https://schema.org", "@type": at_type}
    if facts.get("company_name"):
        out["name"] = facts["company_name"]
    if _url(facts.get("domain", "")):
        out["url"] = _url(facts["domain"])
    if facts.get("description"):
        out["description"] = facts["description"]
    if facts.get("phone"):
        out["telephone"] = facts["phone"]
    addr = facts.get("address")
    if isinstance(addr, dict) and any(addr.get(k) for k in ("street", "city", "state", "zip")):
        out["address"] = {"@type": "PostalAddress"}
        if addr.get("street"):
            out["address"]["streetAddress"] = addr["street"]
        if addr.get("city"):
            out["address"]["addressLocality"] = addr["city"]
        if addr.get("state"):
            out["address"]["addressRegion"] = addr["state"]
        if addr.get("zip"):
            out["address"]["postalCode"] = addr["zip"]
    logo = facts.get("logo") or facts.get("logo_url")
    if logo:
        out["logo"] = logo
    same = [u for u in (facts.get("same_as") or []) if isinstance(u, str) and u.strip()]
    if same:
        out["sameAs"] = same
    cp = facts.get("contact_point")
    if isinstance(cp, dict):
        contact: dict = {"@type": "ContactPoint"}
        for key in ("telephone", "email", "contactType", "url"):
            if cp.get(key):
                contact[key] = cp[key]
        if len(contact) > 1:
            out["contactPoint"] = contact
    return out


def _organization(facts: dict) -> dict | None:
    return _org_core(facts, "Organization") if facts.get("company_name") else None


def _local_business(facts: dict) -> dict | None:
    # LocalBusiness REQUIRES a physical address (schema.org / Google). Emitting a name-only LocalBusiness
    # for a global SaaS is invalid + misleading, so refuse without an address (this drops it for non-local clients).
    addr = facts.get("address")
    has_addr = isinstance(addr, dict) and any(addr.get(k) for k in ("street", "city", "state", "zip"))
    return _org_core(facts, "LocalBusiness") if (facts.get("company_name") and has_addr) else None


def _service(facts: dict) -> dict | None:
    if not facts.get("company_name"):
        return None
    services = facts.get("services") or []
    # Prefer the page/cluster topic, then a real offering; the company name is only the last resort.
    name = ((facts.get("cluster_label") or "").strip()
            or (services[0] if services else "")
            or facts["company_name"])
    out: dict = {"@context": "https://schema.org", "@type": "Service", "name": name,
                 "provider": {"@type": "Organization", "name": facts["company_name"]}}
    if _url(facts.get("domain", "")):
        out["provider"]["url"] = _url(facts["domain"])
    if facts.get("service_type"):
        out["serviceType"] = facts["service_type"]
    area = facts.get("area_served")
    if isinstance(area, list) and area:
        out["areaServed"] = area
    elif isinstance(area, str) and area.strip():
        out["areaServed"] = area.strip()
    if facts.get("description"):
        out["description"] = facts["description"]
    offers = []
    for p in (facts.get("products") or []):
        if isinstance(p, dict) and p.get("name"):
            svc: dict = {"@type": "Service", "name": p["name"]}
            if p.get("description"):
                svc["description"] = p["description"]
            offers.append({"@type": "Offer", "itemOffered": svc})
    if offers:
        out["hasOfferCatalog"] = {"@type": "OfferCatalog",
                                  "name": f"{facts['company_name']} offerings",
                                  "itemListElement": offers}
    return out


def _faq_page(facts: dict) -> dict | None:
    entities = []
    for faq in (facts.get("faqs") or []):
        if faq.get("question") and faq.get("answer"):
            entities.append({"@type": "Question", "name": faq["question"],
                             "acceptedAnswer": {"@type": "Answer", "text": faq["answer"]}})
    if not entities:
        return None    # no grounded Q&A pairs -> refuse (prose path supplies answers upstream)
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entities}


def _aggregate_rating(facts: dict) -> dict | None:
    if facts.get("rating_value") is None or facts.get("review_count") is None:
        return None    # D12: never fabricate review markup
    item_reviewed: dict = {"@type": "Organization"}
    if facts.get("company_name"):
        item_reviewed["name"] = facts["company_name"]
    return {"@context": "https://schema.org", "@type": "AggregateRating",
            "itemReviewed": item_reviewed,
            "ratingValue": facts["rating_value"], "reviewCount": facts["review_count"]}


def _review(facts: dict) -> dict | None:
    if facts.get("rating_value") is None:
        return None    # D12
    item_reviewed: dict = {"@type": "Organization"}
    if facts.get("company_name"):
        item_reviewed["name"] = facts["company_name"]
    return {"@context": "https://schema.org", "@type": "Review",
            "itemReviewed": item_reviewed,
            "reviewRating": {"@type": "Rating", "ratingValue": facts["rating_value"]}}


def _breadcrumb_list(facts: dict) -> dict | None:
    url = _url(facts.get("domain", ""))
    if not url:
        return None
    # "Home" is a structural breadcrumb convention (Google's canonical example), not a company fact.
    items = [{"@type": "ListItem", "position": 1, "name": "Home", "item": url}]
    topic = (facts.get("cluster_label") or "").strip()
    target = (facts.get("target_url") or "").strip()
    if topic and target:      # page-topic crumb so the trail is target-specific, not a lone "Home"
        items.append({"@type": "ListItem", "position": 2, "name": topic, "item": target})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def _website(facts: dict) -> dict | None:
    url = _url(facts.get("domain", ""))
    if not url or not facts.get("company_name"):
        return None
    return {"@context": "https://schema.org", "@type": "WebSite",
            "name": facts["company_name"], "url": url}


def _product(facts: dict) -> dict | None:
    # The primary named offering as a brand-linked Product. Refuses without a grounded product name.
    for p in (facts.get("products") or []):
        if isinstance(p, dict) and p.get("name"):
            out: dict = {"@context": "https://schema.org", "@type": "Product", "name": p["name"]}
            if p.get("description"):
                out["description"] = p["description"]
            if facts.get("company_name"):
                out["brand"] = {"@type": "Brand", "name": facts["company_name"]}
            return out
    return None


_GENERATORS = {
    "Organization": _organization,
    "LocalBusiness": _local_business,
    "Service": _service,
    "FAQPage": _faq_page,
    "Review": _review,
    "AggregateRating": _aggregate_rating,
    "BreadcrumbList": _breadcrumb_list,
    "WebSite": _website,
    "Product": _product,
}

SUPPORTED_SCHEMA_TYPES: frozenset[str] = frozenset(_GENERATORS)

_REQUIRED_PROPS = {
    "Organization": ("name",), "LocalBusiness": ("name",), "Service": ("name", "provider"),
    "FAQPage": ("mainEntity",), "Review": ("itemReviewed", "reviewRating"),
    "AggregateRating": ("ratingValue", "reviewCount"), "BreadcrumbList": ("itemListElement",),
    "WebSite": ("name", "url"), "Product": ("name",),
}


def generate_schema_jsonld(schema_type: str, facts: dict) -> dict | None:
    gen = _GENERATORS.get(schema_type)
    return gen(facts or {}) if gen else None


def validate_jsonld(payload, expected_type: str) -> tuple[bool, str]:
    """FR-GEN-5 well-formedness: parses, right @context/@type, required props present + non-empty.
    A malformed structured asset is a HARD block — it can never be registered."""
    if not isinstance(payload, dict):
        return False, "payload is not a JSON object"
    if payload.get("@context") != "https://schema.org":
        return False, "missing/wrong @context"
    if payload.get("@type") != expected_type:
        return False, f"@type is {payload.get('@type')!r}, expected {expected_type}"
    for prop in _REQUIRED_PROPS.get(expected_type, ()):
        if not payload.get(prop):
            return False, f"required property missing/empty: {prop}"
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as e:
        return False, f"not JSON-serializable: {e}"
    return True, ""
