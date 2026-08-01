# graph.py — LangGraph StateGraph wiring for the full Scout pipeline.
# Purpose: Assembles detection, monitoring, investigation, recommendation, reporting, and delivery nodes into a single compiled graph.
# Scope: Declares nodes, edges, conditional routing for empty-trigger short-circuit, and a merge step that dedupes SOV + blog triggers.
# Consumers: run.py calls build_graph() once per run and invokes the compiled graph with the initial ScoutState.
from langgraph.graph import END, StateGraph

from scout.config import get_config
from scout.keys import make_trigger_key
from scout.nodes.ai_response_analysis import ai_response_analysis
from scout.nodes.blog_monitoring import blog_monitoring
from scout.nodes.citation_adapter import canonical_citation_analysis
from scout.nodes.client_readiness import client_readiness_analysis
from scout.nodes.field_resolution import field_resolution
from scout.nodes.historical_context import historical_context
from scout.nodes.recommendation_gen import recommendation_generation
from scout.nodes.report_gen import report_generation
from scout.nodes.slack_delivery import slack_delivery
from scout.nodes.sov_detection import sov_detection
from scout.nodes.validation_gate import validation_gate
from scout.nodes.web_intelligence import web_intelligence
from scout.nodes.website_diff import website_diff_analysis
from scout.state import ScoutState


def merge_triggers(state: ScoutState) -> dict:
    """Combine SOV and blog investigation triggers into a single deduplicated list keyed by competitor::cluster.
    Returns the merged list under 'investigation_triggers' so downstream nodes iterate over one unified set."""
    sov_triggers = state.get("investigation_triggers", [])
    blog_triggers = state.get("blog_investigation_triggers", [])
    by_key: dict = {}
    merged = []
    for t in sov_triggers:
        by_key[make_trigger_key(t.client_id, t.competitor_name, t.cluster_id)] = t
        merged.append(t)
    attached = 0
    for t in blog_triggers:
        key = make_trigger_key(t.client_id, t.competitor_name, t.cluster_id)
        if key in by_key:
            # Collision: attach the blog post as evidence to the existing SOV trigger instead of dropping it,
            # so "why did they surge" can be grounded in the real post rather than the model's memory.
            if t.blog_post_url or t.blog_post_title:
                by_key[key].blog_evidence.append(
                    {"url": t.blog_post_url, "title": t.blog_post_title, "source": t.detection_source})
                attached += 1
        else:
            by_key[key] = t
            merged.append(t)
    print(f"[merge_triggers] {len(sov_triggers)} SOV + {len(blog_triggers)} blog -> {len(merged)} merged; "
          f"{attached} blog evidence attached")
    return {"investigation_triggers": merged}


def should_investigate(state: ScoutState) -> str:
    """Conditional-edge router: returns 'investigate' when triggers exist, else 'end' to short-circuit the graph.
    Reads state['investigation_triggers'] populated by merge_triggers; no state mutation."""
    if state["investigation_triggers"]:
        return "investigate"
    return "end"


def start_investigation(_: ScoutState) -> dict:
    """No-op fan-out anchor node used to route into the four parallel investigation nodes.
    Returns an empty dict so LangGraph treats it as a state-preserving passthrough."""
    return {}


def build_graph(*, include_delivery: bool = True):
    """Construct and compile the full Scout StateGraph with all nodes and edges wired in execution order.
    Returns a compiled graph ready for .invoke(initial_state); called once per process by run.py."""
    g = StateGraph(ScoutState)
    g.add_node("sov_detection", sov_detection)
    g.add_node("blog_monitoring", blog_monitoring)
    g.add_node("merge_triggers", merge_triggers)
    g.add_node("field_resolution", field_resolution)
    g.add_node("start_investigation", start_investigation)
    g.add_node("website_diff_analysis", website_diff_analysis)
    g.add_node("web_intelligence", web_intelligence)
    citation_node = (
        ai_response_analysis
        if get_config().aivc_recon_legacy_ai_analysis_enabled
        else canonical_citation_analysis
    )
    g.add_node("ai_response_analysis", citation_node)
    g.add_node("client_readiness_analysis", client_readiness_analysis)
    g.add_node("historical_context", historical_context)
    g.add_node("recommendation_generation", recommendation_generation)
    g.add_node("report_generation", report_generation)
    g.add_node("validation_gate", validation_gate)
    if include_delivery:
        g.add_node("slack_delivery", slack_delivery)

    g.set_entry_point("sov_detection")
    g.add_edge("sov_detection", "blog_monitoring")
    g.add_edge("blog_monitoring", "merge_triggers")
    g.add_edge("merge_triggers", "field_resolution")
    g.add_conditional_edges("field_resolution", should_investigate, {
        "investigate": "start_investigation",
        "end": END,
    })
    g.add_edge("start_investigation", "website_diff_analysis")
    g.add_edge("start_investigation", "web_intelligence")
    g.add_edge("start_investigation", "ai_response_analysis")
    g.add_edge("start_investigation", "client_readiness_analysis")
    g.add_edge("start_investigation", "historical_context")
    g.add_edge("website_diff_analysis", "recommendation_generation")
    g.add_edge("web_intelligence", "recommendation_generation")
    g.add_edge("ai_response_analysis", "recommendation_generation")
    g.add_edge("client_readiness_analysis", "recommendation_generation")
    g.add_edge("historical_context", "recommendation_generation")
    g.add_edge("recommendation_generation", "report_generation")
    g.add_edge("report_generation", "validation_gate")
    if include_delivery:
        g.add_edge("validation_gate", "slack_delivery")
        g.add_edge("slack_delivery", END)
    else:
        g.add_edge("validation_gate", END)

    return g.compile()
