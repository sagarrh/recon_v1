import logging
import re

from scout.provenance import build_number_allowlist, unbacked_numerals

log = logging.getLogger(__name__)

_PROMPTS = {
    "llms_summary": (
        "You write one 2-3 sentence factual summary of a company for its llms.txt file, "
        "so AI assistants describe it accurately.",
        "Company facts (use ONLY these; do not invent numbers or claims):\n{facts}\n\n"
        "Demand cluster: {cluster}\n\nWrite the summary.",
    ),
    "faq_answer": (
        "You answer one buyer question for a company's FAQ page in 1-2 factual sentences.",
        "Company facts (use ONLY these; do not invent numbers or claims):\n{facts}\n\n"
        "Question: {question}\n\nWrite the answer.",
    ),
}

_REWRITE_INSTRUCTION = (
    "Your previous draft contained numbers not present in the company facts: {bad}. "
    "Rewrite it using ONLY these facts — drop or replace every unbacked number:\n{facts}\n\n"
    "Previous draft:\n{draft}"
)


def asset_number_allowlist(rec_row: dict, facts: dict) -> set[str]:
    verdict = {"client_id": "", "cluster_id": "", "primary_competitor": "", "field": [], "delta_client": None}
    try:
        return build_number_allowlist(
            verdict, {}, {}, {}, {}, [],
            revenue_context=facts or {},
            revenue_figures={k: (rec_row or {}).get(k) for k in ("revenue_value_usd",)},
        )
    except Exception as e:
        log.warning("[prose] allowlist build failed: %s", e)
        return set()


def check_prose(text: str, allowlist: set[str]) -> list[str]:
    return unbacked_numerals(text or "", allowlist)


# Substance gate — an FAQ answer's text IS what AI engines cite, so a client deliverable must never ship a
# model-limitation leak, a content-free "visit our site" deflection, or a too-thin non-answer.
_META_LIMITATION_PHRASES = (
    "provided company facts", "company facts do not", "do not contain information",
    "does not contain information", "no information about", "as an ai",
    "as a language model", "i cannot", "i can't", "i do not have", "i don't have",
)


def _bare_domain(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip("/")
    if "://" in d:
        d = d.split("://", 1)[1]
    return d.split("/")[0]


def _is_deflection_sentence(sentence: str, dom: str) -> bool:
    s = sentence.lower()
    site_ref = (bool(dom) and dom in s) or "our website" in s or "our site" in s or "our platform" in s
    return ("visit" in s or "learn more" in s or "contact" in s) and site_ref


def is_low_substance_answer(text: str, domain: str = "") -> bool:
    """True when an FAQ answer is unfit for a client deliverable: it leaks a model limitation, or once its
    'visit/contact us' deflection sentences are removed there is almost no substantive claim left. A CTA
    tacked onto a real answer is fine; an answer that is ONLY a CTA (or too thin) is dropped."""
    t = (text or "").strip()
    if not t:
        return True
    if any(p in t.lower() for p in _META_LIMITATION_PHRASES):
        return True
    dom = _bare_domain(domain)
    substantive = [s for s in re.split(r"(?<=[.!?])\s+", t)
                   if s.strip() and not _is_deflection_sentence(s, dom)]
    return sum(len(s.split()) for s in substantive) < 6


def _facts_block(facts: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in (facts or {}).items() if v)


# max_tokens=4000: the synthesis model reasons before answering; a tight budget gets fully
# consumed by reasoning and returns empty content (observed live, HIGH-5).
def fill_prose(kind: str, facts: dict, brief: dict, rec_row: dict, cfg, *,
               synthesis_fn=None, scrape_fn=None) -> tuple[str, dict]:
    if not getattr(cfg, "builder_llms_prose_enabled", False):
        return "", {"skipped": "prose disabled", "attempts": 0}
    if kind not in _PROMPTS:
        return "", {"error": f"unknown prose kind: {kind}", "attempts": 0}
    if synthesis_fn is None:
        from scout.llm import call_synthesis
        synthesis_fn = call_synthesis

    facts = dict(facts or {})
    system, user_tpl = _PROMPTS[kind]
    user = user_tpl.format(facts=_facts_block(facts),
                           cluster=brief.get("cluster_label", ""),
                           question=brief.get("question", ""))
    steps: list[str] = []
    attempts = 0
    try:
        draft = str(synthesis_fn(system, user, expect_json=False,
                                 node_name="asset_builder", max_tokens=4000) or "").strip()
        attempts += 1
    except Exception as e:
        log.warning("[prose] synthesis failed: %s", e)
        return "", {"error": str(e), "attempts": attempts}

    allow = asset_number_allowlist(rec_row, facts)
    bad = check_prose(draft, allow)

    if bad and getattr(cfg, "builder_fact_enrichment_enabled", False):
        if scrape_fn is None:
            from scout.integrations.bright_data import scrape_website
            scrape_fn = scrape_website
        try:
            page = scrape_fn(facts.get("domain", "")) or {}
            excerpt = str(page.get("content", ""))[:3000]
            if excerpt:
                facts["scraped_excerpt"] = excerpt
                allow = asset_number_allowlist(rec_row, facts)
                bad = check_prose(draft, allow)
                steps.append(f"enriched facts via live scrape ({len(excerpt)} chars)")
        except Exception as e:
            steps.append(f"enrichment scrape failed: {e}")

    if bad:
        try:
            draft2 = str(synthesis_fn(
                system,
                _REWRITE_INSTRUCTION.format(bad=", ".join(bad), facts=_facts_block(facts), draft=draft),
                expect_json=False, node_name="asset_builder", max_tokens=4000) or "").strip()
            attempts += 1
            if draft2:
                draft = draft2
                bad = check_prose(draft, allow)
                steps.append("grounding rewrite")
        except Exception as e:
            steps.append(f"rewrite failed: {e}")

    if kind == "faq_answer" and is_low_substance_answer(draft, facts.get("domain", "")):
        steps.append("rejected: low-substance/deflection/limitation-leak")
        return "", {"attempts": attempts, "unbacked_numerals": bad,
                    "grounding_steps": steps, "rejected": "low_substance"}

    return draft, {"attempts": attempts, "unbacked_numerals": bad, "grounding_steps": steps}
