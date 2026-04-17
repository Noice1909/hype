"""Dispatcher node — sets up agent execution (parallel or sequential)."""

from __future__ import annotations

import logging

from diva.graph.state import DivaState

logger = logging.getLogger(__name__)


async def dispatcher_node(state: DivaState) -> dict:
    """Prepare the agent execution batch.

    For parallel: all agents execute in agent_executor via asyncio.gather.
    For sequential: pops one agent at a time, skipping already-completed agents.
    """
    routing = state["routing_decision"]
    mode = routing["execution_mode"]
    pending = state.get("pending_agents", [])

    if mode == "sequential" and routing.get("sequential_plan"):
        # Determine which agents have already produced results
        completed = {r["agent_id"] for r in state.get("agent_results", [])}
        remaining = [a for a in pending if a not in completed]

        if remaining:
            next_agent = remaining[0]
            logger.info("Sequential dispatch: %s (remaining: %s)", next_agent, remaining[1:])
            return {"pending_agents": [next_agent]}

        logger.info("Sequential dispatch: all agents completed")
        return {"pending_agents": []}

    # Parallel: all agents run at once
    logger.info("Parallel dispatch: %s", pending)
    return {"pending_agents": pending}
