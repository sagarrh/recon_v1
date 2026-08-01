# reports — Read-only reporting/audit artifacts over the Scout outcome tables (no LLM).
# Purpose: Presentation layer over scout_outcomes for commercial/audit reporting (attribution, before/after).
# Scope: Pure reads; never mutates pipeline state and never posts to Slack — outputs are artifacts.
# Consumers: scripts/attribution_report.py and other report CLIs; not part of the LangGraph pipeline.
