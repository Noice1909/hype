"""Build the DIVA LangGraph compiled graph."""

from __future__ import annotations

from langgraph.graph import StateGraph, END

from diva.graph.state import DivaState
from diva.graph.nodes.intake import intake_node
from diva.graph.nodes.router import router_node
from diva.graph.nodes.dispatcher import dispatcher_node
from diva.graph.nodes.agent_executor import agent_executor_node
from diva.graph.nodes.synthesizer import synthesizer_node
from diva.graph.nodes.evaluator import evaluator_node
from diva.graph.edges import route_after_dispatch, route_after_agent


def build_graph():
    """Build and compile the DIVA orchestration graph.

    Flow:
      intake -> router -> dispatcher -> agent_executor -> synthesizer -> evaluator -> END
                             ^                               |
                             +--- (sequential: more_agents) -+
    """
    g = StateGraph(DivaState)

    # Nodes
    g.add_node("intake", intake_node)
    g.add_node("router", router_node)
    g.add_node("dispatcher", dispatcher_node)
    g.add_node("agent_executor", agent_executor_node)
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("evaluator", evaluator_node)

    # Edges
    g.set_entry_point("intake")
    g.add_edge("intake", "router")
    g.add_edge("router", "dispatcher")

    g.add_conditional_edges("dispatcher", route_after_dispatch, {
        "run_agents": "agent_executor",
        "done": "synthesizer",
    })

    g.add_conditional_edges("agent_executor", route_after_agent, {
        "more_agents": "dispatcher",
        "all_done": "synthesizer",
    })

    g.add_edge("synthesizer", "evaluator")
    g.add_edge("evaluator", END)

    return g.compile()
