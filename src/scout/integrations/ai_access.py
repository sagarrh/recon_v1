# ai_access.py — Live fetch of a site's AI-access files (llms.txt, robots.txt, AI-bot rules).
# Purpose: Check live whether a domain publishes /llms.txt and whether robots.txt admits AI crawlers — not from the stale GEO snapshot.
# Scope: Stateless plain-requests GETs with a short timeout; swallows errors and returns presence booleans. Never writes.
# Consumers: scout/nodes/client_readiness.py (client site) and scout/nodes/website_diff.py (competitor sites).
import logging

import requests

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "ScoutBot/1.0 (AI-access reader)"}
_TIMEOUT = 12

# Known AI/LLM crawler user-agents an AI-ready robots.txt deliberately addresses.
_AI_CRAWLERS = (
    "gptbot", "oai-searchbot", "chatgpt-user", "claudebot", "claude-web", "anthropic-ai",
    "perplexitybot", "google-extended", "googleother", "ccbot", "bytespider",
    "amazonbot", "applebot-extended", "meta-externalagent", "cohere-ai", "diffbot",
)


def _detect_ai_bots(robots_text: str) -> bool:
    """Return True when robots.txt names a known AI/LLM crawler as a 'User-agent:' directive value (a deliberate policy).
    Line-anchored + comment-stripped so crawler tokens inside Disallow paths, Sitemap URLs, or comments don't false-positive."""
    if not robots_text:
        return False
    for raw in robots_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        field, sep, value = line.partition(":")
        if not sep or field.strip().lower() != "user-agent":
            continue
        agent = value.split("#", 1)[0].strip().lower()
        if agent in _AI_CRAWLERS:
            return True
    return False


def fetch_ai_access_files(domain: str) -> dict | None:
    """Live-check a domain's AI-access files: GET /llms.txt and /robots.txt, derive presence booleans.
    Returns {'llms_txt','robots_txt','ai_bots'} when the host responded, or None when it was unreachable/blank — so callers never read a failed fetch as 'files absent'."""
    d = (domain or "").strip()
    if not d:
        return None
    base = (d if "://" in d else f"https://{d}").rstrip("/")
    out = {"llms_txt": False, "robots_txt": False, "ai_bots": False}
    robots_text = ""
    reached = False
    for key, path in (("llms_txt", "/llms.txt"), ("robots_txt", "/robots.txt")):
        try:
            resp = requests.get(f"{base}{path}", timeout=_TIMEOUT, headers=_HEADERS, allow_redirects=True)
        except requests.RequestException as e:
            log.debug("[ai_access] GET %s%s failed: %s", base, path, e)
            continue
        reached = True   # host answered (200/404/…) — a real signal, distinct from a network failure
        if resp.status_code == 200 and (resp.text or "").strip():
            out[key] = True
            if key == "robots_txt":
                robots_text = resp.text
    if not reached:
        return None
    out["ai_bots"] = _detect_ai_bots(robots_text)
    return out
