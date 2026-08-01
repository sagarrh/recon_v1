# authority_gen.py — Tier 3: deterministic llms.txt + ai_bots (robots.txt AI-directive) generators.
# ai_bots inverts the SAME _AI_CRAWLERS list ai_access uses for detection, so generator and
# verifier can never drift (the round-trip test locks it). llms.txt structure is deterministic;
# the optional prose summary arrives as a parameter (produced by prose.py), never generated here.
from scout.integrations.ai_access import _AI_CRAWLERS, _detect_ai_bots
from scout.utils import ensure_scheme as _url


def generate_llms_txt(facts: dict, prose_summary: str = "") -> str:
    name = (facts or {}).get("company_name", "").strip()
    url = _url((facts or {}).get("domain", ""))
    summary = (prose_summary or "").strip() or (facts or {}).get("description", "").strip()
    lines = [f"# {name}".rstrip()]
    if summary:
        lines += ["", f"> {summary}"]
    lines += ["", "## About", ""]
    if name:
        lines.append(f"- Name: {name}")
    if url:
        lines.append(f"- Website: {url}")
    for svc in (facts or {}).get("services", [])[:8]:
        lines.append(f"- Service: {svc}")
    lines += ["", "## Links", ""]
    if url:
        lines.append(f"- [Home]({url})")
    return "\n".join(lines) + "\n"


def generate_ai_bots_directives() -> str:
    blocks = [f"User-agent: {crawler}\nAllow: /" for crawler in _AI_CRAWLERS]
    return "\n\n".join(blocks) + "\n"


def validate_llms_txt(text: str) -> tuple[bool, str]:
    t = (text or "").strip()
    if not t:
        return False, "empty llms.txt"
    if not t.startswith("# ") or len(t.splitlines()[0]) < 3:
        return False, "llms.txt must start with a '# <name>' H1"
    if "## About" not in t:
        return False, "llms.txt missing the '## About' section"
    if "## Links" not in t:
        return False, "llms.txt missing the '## Links' section"
    return True, ""


def validate_robots_directives(text: str) -> tuple[bool, str]:
    t = (text or "")
    if "User-agent:" not in t or "Allow:" not in t:
        return False, "missing User-agent/Allow directives"
    if not _detect_ai_bots(t):
        return False, "directives not detected by ai_access._detect_ai_bots (generator/verifier drift)"
    return True, ""
