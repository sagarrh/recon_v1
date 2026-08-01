# competitive_narrative.py — grounded LLM competitive-parity narrative (multi-call Tree/Graph-of-Thoughts).
# Reasons over the REAL collected competitor signals to produce a client-vs-competitor parity assessment (an optional
# richer layer on top of the deterministic competitive_gaps). Honesty-first: every branch and the final aggregate pass
# through _ground_narrative against the ORIGINAL signals; only the guarded final narrative is ever returned/stored.
# Consumers: facts_recon.build_client_profile (stores on the recon profile); re-exported by gap_recommendations.
import json
import logging
import re

from scout.provenance import (
    _canon_num,
    _harvest,
    build_entity_allowlist,
    match_allowlisted,
    unbacked_entities,
)

log = logging.getLogger(__name__)

# Common forward-action / connective words that get Capitalized in a summary and are NOT named entities. Exempted by
# EXACT match (never substring) so an invented brand like "Addepar" is not accidentally excused by containing "add".
_SUMMARY_SAFE_WORDS = frozenset({
    "grow", "claim", "establish", "match", "mirror", "publish", "add", "build", "create", "improve", "increase",
    "close", "reach", "secure", "expand", "ensure", "maintain", "prioritize", "focus", "start", "consider", "develop",
    "strengthen", "gain", "earn", "collect", "list", "register", "get", "set", "keep", "make", "drive", "boost",
    "raise", "capture", "with", "across", "while", "where", "when", "these", "those", "their", "them", "your", "you",
    "the", "this", "that", "both", "each", "once", "after", "before", "given", "note", "reviews", "review", "rating",
    "ratings", "profile", "profiles", "directory", "directories", "presence", "parity", "competitor", "competitors",
    "client", "capterra", "trustpilot", "crunchbase", "linkedin", "wikidata", "wikipedia", "glassdoor",
})

# Generic capitalized terms + inflected action verbs the entity regex greedily grabs (e.g. "Knowledge Graph",
# "Drive G2", "Growing G2") — NOT named entities. Used token-wise so a phrase built entirely of these is exempt.
_GENERIC_TERMS = frozenset({
    "knowledge", "graph", "rich", "results", "answer", "engine", "entity", "footprint", "coverage", "authority",
    "resolution", "disambiguation", "encyclopedic", "structured", "canonical", "verified", "signal", "signals",
    "platform", "platforms", "volume", "share", "gap", "gaps", "product", "products", "page", "pages", "third-party",
    "growing", "driving", "closing", "matching", "establishing", "securing", "launching", "creating", "publishing",
    "building", "improving", "lifting", "capturing", "generating", "maintaining", "achieving", "anchoring",
    "collecting", "beginning", "holding", "leaving", "giving", "scaling", "adding", "g2", "softwareadvice", "x",
    "url", "qid", "id", "api", "ai", "seo", "saas", "roi", "ui", "ux", "b2b", "sku", "faq", "cta", "tier",
})

# A quantity is a digit run NOT glued to a leading letter (so "G2"/"B2B" contribute no number) and captured whole
# (so "2115" is not mis-split). A trailing letter is allowed ONLY if it is a lone magnitude/multiplier suffix at a
# word boundary ("100k", "5x") — so those fabricated magnitudes are still caught, while "6sense" is not read as "6".
_QTY_RE = re.compile(r"(?<![A-Za-z0-9.,])[+\-]?\d[\d,]*(?:\.\d+)?")
_MAG = frozenset("kmbx")


def _free_unbacked_numerals(text: str, allow: set) -> list:
    """Free-standing quantities in text that don't trace to the allowlist (same tolerance as the shared gate)."""
    out = []
    t = text or ""
    for m in _QTY_RE.finditer(t):
        nxt = t[m.end():m.end() + 1]
        if nxt.isalnum() and not (nxt.lower() in _MAG and not t[m.end() + 1:m.end() + 2].isalnum()):
            continue                                     # digit glued into a longer identifier (6sense) -> not a quantity
        c = _canon_num(m.group())
        if c and not match_allowlisted(c, allow):
            out.append(c)
    return out


def _flag_entities(text: str, allow_ents: set, known: set, url_toks=frozenset()) -> list:
    """Unbacked named entities in text, MINUS phrases built entirely of benign tokens: safe words, generic terms,
    known-competitor name tokens, and tokens that appear in a collected signal URL (so 'SciQuest' from a real
    crunchbase slug is grounded). Still catches a fabricated brand ('Rossum'). Over-flagging destroys real narratives
    far more often than it catches a fabrication, so single generic words (sentence-initial 'Closing') are exempt too;
    the numeral gate + structured-action competitor check cover the high-value fabrication cases."""
    benign = _SUMMARY_SAFE_WORDS | _GENERIC_TERMS | frozenset(url_toks)
    benign |= {t for n in known for t in (n or "").lower().split()}
    return [e for e in unbacked_entities(text, allow_ents, known) if not all(t in benign for t in e.split())]


def _ground_narrative(narrative: dict, client_row: dict, competitor_signals: list) -> dict:
    """Code-enforced honesty gate for the LLM parity narrative (D12/D16). Quarantines the WHOLE narrative to {} when
    the free-text summary asserts a number or named entity that doesn't trace to the collected signals, and drops any
    parity_action naming an unknown competitor or citing an evidence URL that wasn't in the input. Prompt rules alone
    are not enforcement — this is."""
    summary = str(narrative.get("summary") or "")

    allow_ents = build_entity_allowlist(competitor_signals, client_row)
    names = {c.get("name") for c in competitor_signals if c.get("name")}
    known = names | {client_row.get("company_name")}
    known_l = {(n or "").lower() for n in names}

    # Numerals + evidence URLs are bound PER COMPETITOR (not one flat pool) so an action can't reattribute a fact from
    # one competitor onto another — the aggregate's main fabrication mode. The free-text summary can't be attributed,
    # so it validates against the global union.
    client_nums: set = set()
    _harvest({"rating_value": client_row.get("rating_value"), "review_count": client_row.get("review_count")}, client_nums)
    nums_by_name: dict = {}
    urls_by_name: dict = {}
    for c in competitor_signals:
        nm = (c.get("name") or "").lower()
        if not nm:
            continue
        cn: set = set()
        _harvest(c, cn)
        nums_by_name.setdefault(nm, set()).update(cn)
        cu = set(c.get("same_as") or [])
        if c.get("rating_source"):
            cu.add(c["rating_source"])
        urls_by_name.setdefault(nm, set()).update(cu)
    all_nums = set(client_nums).union(*nums_by_name.values()) if nums_by_name else set(client_nums)
    all_urls = set().union(*urls_by_name.values()) if urls_by_name else set()
    # slug tokens from every collected URL ground an entity mentioned in prose (e.g. 'SciQuest' from a crunchbase slug).
    url_toks = frozenset(t for u in (all_urls | set(client_row.get("same_as") or []))
                         for t in re.split(r"[^a-z0-9]+", (u or "").lower()) if len(t) >= 3)

    bad_nums = _free_unbacked_numerals(summary, all_nums)
    bad_ents = _flag_entities(summary, allow_ents, known, url_toks)
    if bad_nums or bad_ents:
        log.debug("[gap] parity narrative quarantined — unbacked numbers=%s entities=%s", bad_nums, bad_ents)
        return {}                                       # a fabricated specific in the summary voids the narrative

    actions = []
    for a in (narrative.get("parity_actions") or []):
        if not isinstance(a, dict):
            continue
        comps = [c.lower() for c in (a.get("competitors") or []) if isinstance(c, str)]
        if comps and not all(c in known_l for c in comps):
            continue                                    # names a competitor we didn't collect -> drop the action
        if comps:                                       # bind facts to the competitor(s) THIS action names
            ok_nums = set(client_nums)
            ok_urls: set = set()
            for c in comps:
                ok_nums |= nums_by_name.get(c, set())
                ok_urls |= urls_by_name.get(c, set())
        else:
            ok_nums, ok_urls = all_nums, all_urls       # no competitor named -> nothing to misattribute; global pool
        ev = a.get("evidence") or ""
        if ev and ev not in ok_urls:
            continue                                    # evidence URL not owned by a named competitor -> drop the action
        act = str(a.get("action") or "")                # the action free text is rendered verbatim -> ground it too
        if _free_unbacked_numerals(act, ok_nums) or _flag_entities(act, allow_ents, known, url_toks):
            continue                                    # a fabricated / misattributed figure in the action -> drop it
        actions.append(a)

    conf = narrative.get("confidence")
    conf = conf if conf in ("high", "medium", "low") else "low"
    return {"summary": summary, "parity_actions": actions,
            "confidence": "low" if not actions else conf}   # no grounded action -> never claim high confidence


_CONF_RANK = {"high": 2, "medium": 1, "low": 0}

# Tree-of-Thoughts branches: each lens reweights the SAME grounded signals from a distinct angle (diversity is data-
# conditioned, not sampling-conditioned — deterministic even at temperature 0). The list is the hard cap on branch count.
LENSES = [
    {"key": "reviews_first",
     "focus": "Third-party review presence and rating parity. Prioritise competitors carrying a rating_value/"
              "review_count the client lacks; the highest-leverage move is closing the reviewed-profile gap."},
    {"key": "identity_authority_first",
     "focus": "Entity/identity authority. Prioritise the sameAs identity profiles (Crunchbase, LinkedIn, Wikidata) "
              "competitors hold and the client lacks; the gap is entity resolution and disambiguation."},
    {"key": "ai_citation_leverage",
     "focus": "Highest AI-citation leverage. Rank each candidate parity action by how much it changes whether an "
              "answer engine can resolve, trust, and cite the client versus each competitor."},
    {"key": "coverage_breadth",
     "focus": "Breadth. Enumerate every distinct parity gap across ALL supplied competitors before pruning, so no "
              "collected signal is left unaddressed."},
]


def _client_block(client_row: dict) -> dict:
    return {
        "rating_value": client_row.get("rating_value"),
        "review_count": client_row.get("review_count"),
        "same_as": client_row.get("same_as") or [],
        "product_names": [p.get("name") if isinstance(p, dict) else p for p in (client_row.get("products") or [])],
    }


def _signals(client_row: dict, competitor_signals: list) -> dict:
    return {"client": {**_client_block(client_row), "name": client_row.get("company_name")},
            "competitors": competitor_signals}


def _call(synthesis_fn, prompt_name: str, payload: dict, node_name: str):
    """One synthesis call; returns the parsed dict or None (non-dict). Raises on synthesis/prompt failure — callers catch."""
    from scout.llm import load_prompt
    out = synthesis_fn(load_prompt(prompt_name), json.dumps(payload), expect_json=True, node_name=node_name)
    return out if isinstance(out, dict) else None


def _call_retry(synthesis_fn, prompt_name: str, payload: dict, node_name: str, attempts: int = 2):
    """_call with a bounded retry (for the judge/aggregate stages, which the live test saw fail transiently). Surfaces
    each attempt's exception at WARNING so a fallback is never silent; returns None once exhausted (caller degrades)."""
    for i in range(attempts):
        try:
            out = _call(synthesis_fn, prompt_name, payload, node_name)
            if out is not None:
                return out
            log.warning("[gap] %s attempt %d/%d returned non-dict", node_name, i + 1, attempts)
        except Exception as e:
            log.warning("[gap] %s attempt %d/%d failed: %s", node_name, i + 1, attempts, e)
    return None


def _rank_fallback(grounded: list) -> list:
    """Deterministic best-first ordering when the judge is off/fails: more actions, then higher confidence, then lens order."""
    return sorted(range(len(grounded)),
                  key=lambda i: (-len(grounded[i].get("parity_actions") or []),
                                 -_CONF_RANK.get(grounded[i].get("confidence"), 0), i))


def _judge_ranking(synthesis_fn, signals: dict, survivors: list) -> list:
    """Score the survivors (separate call) and return a best-first branch ordering; deterministic fallback on any failure.
    The judge output is structured scores only — ephemeral, never grounded, never stored (so it cannot fabricate)."""
    grounded = [g for _, g in survivors]
    candidates = [{"branch": i, "lens_key": lk, "summary": g.get("summary"),
                   "parity_actions": g.get("parity_actions"), "confidence": g.get("confidence")}
                  for i, (lk, g) in enumerate(survivors)]
    out = _call_retry(synthesis_fn, "scout-competitive-parity-judge",
                      {"signals": signals, "candidates": candidates}, "competitive_parity_judge")
    ranking = out.get("ranking") if isinstance(out, dict) else None
    if isinstance(ranking, list) and sorted(ranking) == list(range(len(survivors))):
        return ranking
    return _rank_fallback(grounded)


def competitive_narrative(client_row: dict, competitor_signals, gaps=None, *, synthesis_fn=None, cfg=None) -> dict:
    """Grounded multi-call Tree/Graph-of-Thoughts parity narrative over the REAL collected competitor signals: generate N
    lens-diverse candidate branches, ground each, judge/prune the survivors, and aggregate the top-K into one narrative —
    re-grounded against the original signals. Returns {} when there are no signals, on any failure, or when nothing
    survives grounding. Never raises. Only the guarded final narrative surfaces; branches/judge are ephemeral."""
    if not competitor_signals:
        return {}
    if synthesis_fn is None:
        from scout.llm import call_synthesis
        synthesis_fn = call_synthesis
    if cfg is None:
        from scout.config import get_config
        cfg = get_config()
    try:
        return _orchestrate(client_row, competitor_signals, gaps, synthesis_fn, cfg)
    except Exception as e:
        log.warning("[gap] competitive narrative failed: %s", e)
        return {}


def _orchestrate(client_row: dict, competitor_signals: list, gaps, synthesis_fn, cfg) -> dict:
    n = max(1, min(getattr(cfg, "gap_competitive_branches", 3), len(LENSES)))   # lens list is the hard cap on cost
    comp_gaps = [g for g in (gaps or []) if isinstance(g, dict) and g.get("source") == "competitive"]
    survivors: list = []                                    # (lens_key, grounded_dict) in lens order
    for lens in LENSES[:n]:
        payload = {"client_name": client_row.get("company_name"), "client": _client_block(client_row),
                   "competitors": competitor_signals, "detected_gaps": comp_gaps,
                   "lens": lens["focus"], "lens_key": lens["key"]}
        try:
            out = _call(synthesis_fn, "scout-competitive-parity", payload, "competitive_parity_branch")
        except Exception as e:
            log.warning("[gap] parity branch %s failed: %s", lens["key"], e)
            continue
        if out is None:
            continue
        g = _ground_narrative(out, client_row, competitor_signals)
        if g.get("parity_actions") or g.get("summary"):
            survivors.append((lens["key"], g))

    if not survivors:
        return {}
    if len(survivors) == 1:
        return survivors[0][1]                             # one grounded branch -> return it (no judge/aggregate cost)

    grounded = [g for _, g in survivors]
    signals = _signals(client_row, competitor_signals)
    ranking = (_judge_ranking(synthesis_fn, signals, survivors)
               if getattr(cfg, "gap_competitive_judge_enabled", True) else _rank_fallback(grounded))
    best = grounded[ranking[0]]                             # highest-ranked grounded survivor — the aggregate fallback

    top_k = max(1, getattr(cfg, "gap_competitive_aggregate_top_k", 2))
    top = [grounded[i] for i in ranking[:top_k]]
    agg = _call_retry(synthesis_fn, "scout-competitive-parity-aggregate",
                      {"signals": signals, "candidates": top}, "competitive_parity_aggregate")
    if agg is not None:
        final = _ground_narrative(agg, client_row, competitor_signals)   # re-ground the merge vs the ORIGINAL signals
        if final.get("parity_actions") or final.get("summary"):
            return final
    return best                                            # aggregate failed / non-dict / quarantined -> best survivor
