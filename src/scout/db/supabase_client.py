# supabase_client.py — Lazy Supabase client factory for the SED database.
# Purpose: Provides a memoized supabase-py client built from ScoutConfig credentials.
# Scope: Singleton accessor only; raises if env vars are missing so bootstrap fails fast.
# Consumers: scout/db/sync.py, scripts/inspect_supabase.py — any code that needs a live Supabase connection.
from scout.config import get_config

_client = None


def get_sed_client():
    """Return the memoized Supabase client for the SED project, constructing it on first access.
    Raises RuntimeError when SUPABASE_URL or SUPABASE_KEY are missing from the environment."""
    global _client
    if _client is None:
        config = get_config()
        if not config.supabase_url or not config.supabase_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in .env"
            )
        from supabase import create_client
        _client = create_client(config.supabase_url, config.supabase_key)
    return _client
