# utils.py — Shared helper utilities for the Scout agent.
# Purpose: Hosts small cross-cutting helpers that don't warrant their own module.
# Scope: Dep-free (heavy imports are lazy) so db/ modules can import it without cycles or extra deps.
# Consumers: scout/llm.py, db/ modules, builders, reports, and scripts that reuse these helpers.
import json
import logging
from datetime import UTC, date, datetime, timedelta


def is_transient(e: Exception) -> bool:
    """Return True when the exception is an OpenAI-client retryable transient error.
    Used by LLM call-sites to gate retry loops against rate-limit, server, and connection errors."""
    import openai
    return isinstance(e, (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError))


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string suitable for Supabase timestamptz columns.
    Always timezone-aware UTC so Supabase stores it without ambiguity."""
    return datetime.now(UTC).isoformat()


def normalize_domain(d: str) -> str:
    """Strip scheme + www. prefix + trailing slash and lowercase so domains align with competitor_domain entries.
    Returns empty string when input is None or empty."""
    if not d:
        return ""
    s = str(d).strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


def parse_json_cell(value):
    """Coerce a Supabase JSON cell (str/list/dict/None) into a Python object, returning None on parse failure."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def safe_float(v, default=None):
    """Coerce a value to float, returning `default` when it is None or non-numeric."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_week_date(value) -> str:
    """Bucket an ISO-8601 timestamp into its Monday-based week date string (YYYY-MM-DD).
    Falls back to today on parse failure so a malformed row still lands somewhere reasonable."""
    if not value:
        return str(date.today())
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return str(dt.date() - timedelta(days=dt.weekday()))
    except Exception:
        return str(date.today())


def parse_date(value) -> date | None:
    """Parse a YYYY-MM-DD(...) value into a date, or None when it is missing/unparseable.
    Tolerates full ISO timestamps by slicing the leading 10 chars."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def ensure_scheme(domain: str) -> str:
    """Ensure an http(s):// scheme on a bare domain; returns '' for empty input.
    Leaves an existing scheme untouched; strips a trailing slash before adding https://."""
    d = (domain or "").strip().rstrip("/")
    if not d:
        return ""
    return d if "://" in d else f"https://{d}"


def print_section(title: str):
    """Print a 70-char '=' banner around a title for visual separation in console output.
    Purely cosmetic; no logical effect."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def init_logging():
    """Configure root logging at INFO with a '<level> <name>: <message>' format for CLI scripts."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def sb():
    """Return the memoized Supabase client; lazy-imported so importing this module never requires the package.
    Raises whatever get_sed_client raises (RuntimeError on missing env vars)."""
    from scout.db.supabase_client import get_sed_client
    return get_sed_client()
