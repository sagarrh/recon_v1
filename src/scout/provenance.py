# provenance.py — Pure per-verdict numeric-provenance helpers for the validation gate.
# Purpose: Build the allowlist of numbers that legitimately trace to a verdict's structured evidence, and (R2-3) extract/normalize/match numerals so unbacked figures can be repaired or quarantined.
# Scope: Pure functions, no LLM / no I/O — importable by any consumer (like scout/keys.py). Mirrors the per-verdict evidence union recommendation_gen assembles.
# Consumers: scout/nodes/validation_gate.py — R2-2 builds the allowlist; R2-3 matches each artifact numeral against it.
import re

from scout.keys import make_trigger_key

_NUM_RE = re.compile(r"[+\-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|[+\-]?\d+(?:\.\d+)?")   # signed integers/decimals, with optional comma-thousands; used to harvest numbers from evidence text


def _canon_num(value) -> str | None:
    """Canonicalize a numeric value to its absolute-magnitude comparison string ('4.20'->'4.2', '2.0'->'2').
    Returns None for non-numeric / non-finite values so only real numbers enter the allowlist."""
    try:
        f = abs(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf guard
        return None
    if f == int(f):
        return str(int(f))
    return (f"{f:.6f}").rstrip("0").rstrip(".")


def _harvest(obj, out: set) -> None:
    """Walk a nested dict/list/scalar structure, adding every numeric scalar (and numerals embedded in strings) to out.
    Booleans are skipped (bool subclasses int) so True/False never inject 0/1 into the allowlist."""
    if obj is None or isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        c = _canon_num(obj)
        if c is not None:
            out.add(c)
        return
    if isinstance(obj, str):
        for tok in _NUM_RE.findall(obj):
            c = _canon_num(tok)
            if c is not None:
                out.add(c)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _harvest(v, out)
        return
    if isinstance(obj, (list, tuple, set)):
        for v in obj:
            _harvest(v, out)


# ── Named-entity provenance (siblings of the numeric allowlist; for the claim/reframe gate) ──
_ENTITY_RE = re.compile(r'\b[A-Z][A-Za-z0-9&.\-]{2,}(?:\s+[A-Z][A-Za-z0-9&.\-]+){0,3}\b')
_ENTITY_STOP = {"the", "this", "that", "these", "those", "for", "with", "and", "from", "when",
                "where", "which", "their", "there", "after", "given", "note", "learn", "visit"}


def _harvest_entities(obj, out: set) -> None:
    """Walk a nested structure adding named phrases (capitalized single/multiword: companies, products, events),
    lowercased, from every string. Sibling of _harvest for numbers — used to build the evidence-entity allowlist."""
    if obj is None or isinstance(obj, bool):
        return
    if isinstance(obj, str):
        for m in _ENTITY_RE.findall(obj):
            tok = m.strip().lower()
            if len(tok) >= 3 and tok not in _ENTITY_STOP:
                out.add(tok)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _harvest_entities(v, out)
        return
    if isinstance(obj, (list, tuple, set)):
        for v in obj:
            _harvest_entities(v, out)


def build_entity_allowlist(*evidence_objs) -> set:
    """The set of named entities/phrases (lowercased) that legitimately trace to collected evidence — blog posts,
    Bright Data events, AI-citation claims, website changes. The claim gate reframes a named event NOT in it."""
    out: set = set()
    for obj in evidence_objs:
        _harvest_entities(obj, out)
    return out


# Report-structure vocabulary + GEO terms that are not external named claims (section headers etc.).
_REPORT_VOCAB = {
    "sov movement summary", "displacement analysis", "investigation findings", "root cause assessment",
    "recommended actions", "measurement plan", "revenue context", "client readiness", "gap analysis",
    "sov", "geo", "serp", "kpi", "kpis", "faqpage", "faq", "json-ld", "schema", "rich results test",
    "web", "content", "marketing", "partners", "owner", "title", "timeline", "escalation", "deliverables",
    "forecast", "monday", "google", "next", "pre-launch", "re-scan", "direction", "client", "competitor",
    "priority", "confidence", "sub-steps",
}


def extract_claim_entities(text: str) -> set:
    """Named entities/phrases asserted in report prose, minus report-structure vocabulary — the candidate set of
    external product/event/company claims whose provenance the gate checks against the evidence allowlist."""
    ents: set = set()
    _harvest_entities(text or "", ents)
    return {e for e in ents if e not in _REPORT_VOCAB}


def unbacked_entities(text: str, allowlist: set, known_names: set) -> list:
    """Named claims in the prose that trace to NO collected evidence and are not a known competitor/client name.
    Lenient + substring-aware: an entity is backed if it equals/contains/is-contained-by any allowlisted phrase,
    so a cited event never trips; only a truly-absent specific (e.g. an invented 'Rossum') is returned."""
    known = {(n or "").lower() for n in (known_names or set()) if n}
    out = []
    for e in extract_claim_entities(text):
        if any(e == n or e in n or n in e for n in known):
            continue
        if any(e == a or e in a or a in e for a in allowlist):
            continue
        out.append(e)
    return out


def qualify_unbacked_claims(text: str, unbacked: list) -> tuple[str, int]:
    """Enforce path: replace each unbacked named specific with a neutral 'a recent development' so the prose keeps
    a plausible cause without asserting an unverified specific. Returns (text, replacements). Longest phrase first."""
    if not text or not unbacked:
        return text, 0
    n = 0
    out = text
    for e in sorted(set(unbacked), key=len, reverse=True):
        out, k = re.compile(re.escape(e), re.IGNORECASE).subn("a recent development", out)
        n += k
    return out, n


def _harvest_model(obj, out: set) -> None:
    """Dump a Pydantic model (or dict) to JSON-able form and harvest every numeric token from it into out.
    No-op when obj is None so a missing evidence entry contributes nothing to the allowlist."""
    if obj is None:
        return
    d = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    _harvest(d, out)


def build_number_allowlist(verdict, wc_all, tps_all, aic_all, cr_all, sov_records, revenue_context=None, revenue_figures=None, asset_attribution=None) -> set[str]:
    """Collect every numeric token tracing to this verdict's evidence: primary + every field competitor + SOV records + injected readiness/revenue.
    Returns canonical magnitude strings; a numeral absent from this set is unbacked (R2-3 repairs/quarantines it). Mirrors recommendation_gen's per-verdict union, keyed by verdict.field."""
    out: set[str] = set()
    v = verdict.model_dump(mode="json") if hasattr(verdict, "model_dump") else dict(verdict)
    client_id = v.get("client_id") or ""
    cluster_id = v.get("cluster_id") or ""
    primary = v.get("primary_competitor") or ""

    # Primary competitor evidence — the same trigger key recommendation_gen unions.
    pkey = make_trigger_key(client_id, primary, cluster_id)
    _harvest_model(wc_all.get(pkey), out)
    _harvest_model(tps_all.get(pkey), out)
    _harvest_model(aic_all.get(pkey), out)

    # Field competitors — each delta_pp plus the field competitor's own evidence.
    for f in (v.get("field") or []):
        if not isinstance(f, dict):
            continue
        _harvest(f.get("delta_pp"), out)
        comp = f.get("competitor")
        if comp:
            fkey = make_trigger_key(client_id, comp, cluster_id)
            _harvest_model(wc_all.get(fkey), out)
            _harvest_model(tps_all.get(fkey), out)
            _harvest_model(aic_all.get(fkey), out)

    # SOV numbers for this client-cluster (verdict delta + matching tracking records).
    _harvest(v.get("delta_client"), out)
    for r in (sov_records or []):
        d = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r)
        if d.get("client_id") == client_id and d.get("cluster_id") == cluster_id:
            _harvest(d.get("client_sov_this_week"), out)
            _harvest(d.get("client_sov_change_vs_avg"), out)
            _harvest(d.get("sov_score"), out)

    # Injected blocks — client readiness figures + revenue figures stamped onto the rec by _stamp_context.
    _harvest_model(cr_all.get(client_id), out)
    _harvest(revenue_context, out)
    _harvest(revenue_figures, out)
    _harvest(asset_attribution, out)
    return out


# R2-3 — numeral extraction, normalization, and exemptions so structural numerals never trip provenance.
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")              # 4-digit years
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")       # ISO dates (e.g. sync_date)
_RANGE_RE = re.compile(r"\b\d+\s*[-–]\s*\d+\b")      # 'N-M' ranges, e.g. the '3-5 actions' cardinality
_SECTION_RE = re.compile(r"(?m)^\s*\d+\.\s")             # numbered report sections '1. ' ... '6. '
_RUNID_RE = re.compile(r"\b[0-9a-fA-F]{6,}\b")           # short hex run-id fragments / hashes
_EXEMPT_PATTERNS = (_YEAR_RE, _ISO_DATE_RE, _RANGE_RE, _SECTION_RE, _RUNID_RE)
_NUMERAL_SPAN_RE = re.compile(r"[+\-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:pp|%)?|[+\-]?\d+(?:\.\d+)?\s*(?:pp|%)?")   # number + optional unit suffix (with comma-thousands), for rewriting


def _exempt_spans(text: str) -> list[tuple[int, int]]:
    """Return the character spans covered by any structural-exemption pattern (years, dates, ranges, sections, run-ids).
    A numeral overlapping one of these spans is structural, not a claim, and never counts as unbacked."""
    spans: list[tuple[int, int]] = []
    for pat in _EXEMPT_PATTERNS:
        for mt in pat.finditer(text or ""):
            spans.append((mt.start(), mt.end()))
    return spans


def extract_numerals(text: str) -> list[str]:
    """Return the canonical, non-exempt numerals in text (signed ints/decimals; pp/% suffixes ignored).
    Drops structural numerals (years, ISO dates, 'N-M' ranges, numbered sections, hex run-ids) so they never trip the gate."""
    if not text:
        return []
    exempt = _exempt_spans(text)
    out: list[str] = []
    for mt in _NUM_RE.finditer(text):
        s, e = mt.start(), mt.end()
        if any(s < xe and xs < e for (xs, xe) in exempt):
            continue
        c = _canon_num(mt.group())
        if c is not None:
            out.append(c)
    return out


def match_allowlisted(token, allowlist: set[str], tol_abs: float = 0.5, tol_rel: float = 0.05) -> bool:
    """Return True when a numeral traces to the allowlist: exact canonical match or within ±tol_abs / tol_rel.
    Non-numeric tokens return True (not our concern); display rounding (4.2 vs 4.20) and unit suffixes never false-trip."""
    canon = _canon_num(token)
    if canon is None:
        return True
    if canon in allowlist:
        return True
    try:
        f = float(canon)
    except ValueError:
        return True
    for a in allowlist:
        try:
            av = float(a)
        except ValueError:
            continue
        if abs(av - f) <= tol_abs or (av and abs(av - f) / abs(av) <= tol_rel):
            return True
    return False


def unbacked_numerals(text: str, allowlist: set[str]) -> list[str]:
    """Return the distinct canonical numerals in text that are neither structurally exempt nor in the allowlist.
    Convenience over extract_numerals + match_allowlisted; preserves first-seen order for stable note/metric output."""
    out: list[str] = []
    seen: set[str] = set()
    for c in extract_numerals(text):
        if c not in seen and not match_allowlisted(c, allowlist):
            seen.add(c)
            out.append(c)
    return out


def rewrite_unbacked_client(text: str, allowlist: set[str]) -> tuple[str, int]:
    """Replace each unbacked numeral (+ optional pp/% suffix) in a client summary with a qualitative 'some', leaving backed/exempt numbers intact.
    Returns (new_text, count_rewritten); deterministic, used only when numeric provenance is enforced (shadow mode off)."""
    if not text:
        return text, 0
    exempt = _exempt_spans(text)
    count = 0
    for mt in reversed(list(_NUMERAL_SPAN_RE.finditer(text))):
        s, e = mt.start(), mt.end()
        if any(s < xe and xs < e for (xs, xe) in exempt):
            continue
        core = _NUM_RE.search(mt.group())
        c = _canon_num(core.group()) if core else None
        if c is None or match_allowlisted(c, allowlist):
            continue
        text = text[:s] + "some" + text[e:]
        count += 1
    return text, count
