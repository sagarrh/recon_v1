# keys.py — Canonical client-scoped trigger-key builder shared across the pipeline.
# Purpose: One key so a (client, competitor, cluster) triple is globally unique within a run, even when GEO group keys ('0','1','3'...) repeat across clients.
# Scope: Pure string helper; cluster_id is kept RAW (ai_response_analysis re-joins GEO by the raw value).
# Consumers: graph.merge_triggers, web_intelligence/website_diff/ai_response_analysis nodes, recommendation_gen, sed_writer.


def make_trigger_key(client_id: str, competitor_name: str, cluster_id: str) -> str:
    """Return the canonical client-scoped trigger key '{client_id}::{competitor}::{cluster_id}'.
    client_id disambiguates clusters that share a GEO group key (e.g. '3') across different clients."""
    return f"{client_id}::{competitor_name}::{cluster_id}"


def make_cluster_key(client_id: str, cluster_id: str) -> str:
    """Return the client-scoped per-cluster key '{client_id}::{cluster_id}' (no competitor).
    Keys per-cluster history/context that spans the whole competitive field."""
    return f"{client_id}::{cluster_id}"
