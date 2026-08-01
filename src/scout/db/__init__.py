# __init__.py — scout.db subpackage marker.
# Purpose: Groups data-access modules: local SQLite cache, Supabase client, readers/writers, and SED column mapping.
# Scope: No runtime logic; importing the package does not eagerly import submodules.
# Consumers: run.py (reader/writer), scout/nodes/* that persist state, scripts/sync_from_sed.py.
