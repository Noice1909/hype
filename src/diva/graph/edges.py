"""Conditional edge functions for the LangGraph graph."""

from __future__ import annotations

from diva.graph.state import DivaState


def route_after_dispatch(state: DivaState) -> str:
    """If there are agents to execute, go to agent_executor. Otherwise skip to synthesizer."""
    if state.get("pending_agents"):
        return "run_agents"
    return "done"


def route_after_agent(state: DivaState) -> str:
    """After agent execution, check if sequential plan has more agents."""
    rd = state.get("routing_decision", {})
    if rd.get("execution_mode") == "sequential" and rd.get("sequential_plan"):
        # Check if there are agents that haven't produced results yet
        all_agents = rd.get("agents", [])
        completed = {r["agent_id"] for r in state.get("agent_results", [])}
        remaining = [a for a in all_agents if a not in completed]
        if remaining:
            return "more_agents"
    return "all_done"
