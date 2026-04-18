"""Router node — classifies intent and selects which agents to invoke.

Phase 2: LLM-based intent classification with structured output.
"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from diva.agents.registry import AgentRegistry
from diva.graph.state import DivaState, RoutingDecision
from diva.llm.provider import get_llm

logger = logging.getLogger(__name__)

_registry: AgentRegistry | None = None

_ROUTER_SYSTEM_PROMPT = """\
You are the DIVA Router — an intent classifier for a multi-agent system.

Your job: given a user message, decide which agent(s) should handle it.

## Available agents
{agent_descriptions}

## Rules
1. Select 1–{max_agents} agents that are best suited to answer the query.
2. If the query clearly maps to a single agent's scope, pick just that one.
3. If the query spans multiple agents and the results are independent, use "parallel".
4. If one agent's output is needed as input for another, use "sequential" and provide a sequential_plan.
5. When in doubt, prefer fewer agents over more.
6. For greetings (hi, hello, hey), general questions (what can you do, who are you), irrelevant topics (poems, jokes, weather, geography), session summaries, and "remember" requests — ALWAYS route to "diva". The diva agent is the personality of the system.
7. Do NOT route greetings or irrelevant questions to data agents like neo4j, mongodb, or confluence.

## Response format
Respond with ONLY valid JSON (no markdown, no explanation outside the JSON):
{{
  "agents": ["agent_id_1"],
  "execution_mode": "parallel",
  "reasoning": "brief explanation",
  "sequential_plan": null
}}

For sequential mode, include a plan:
{{
  "agents": ["agent_a", "agent_b"],
  "execution_mode": "sequential",
  "reasoning": "brief explanation",
  "sequential_plan": [{{"agent": "agent_a", "feeds_into": "agent_b"}}]
}}
"""


def configure_router(registry: AgentRegistry) -> None:
    """Set module-level registry. Called once during app startup."""
    global _registry
    _registry = registry


async def router_node(state: DivaState) -> dict:
    """Classify user intent and decide which agents to dispatch.

    Fast-path: when ``cypher_override`` is set in state (UI sent a pre-
    written Cypher), skip the LLM classification and route directly to
    neo4j. The agent_executor sees ``cypher_override`` and runs the
    query verbatim instead of the ReAct loop.
    """
    if state.get("cypher_override"):
        routing_decision = RoutingDecision(
            agents=["neo4j"],
            execution_mode="parallel",
            reasoning="cypher fast-path: running user-provided query verbatim",
            sequential_plan=None,
        )
        logger.info("Router: cypher fast-path — routing to neo4j")
    else:
        routing_decision = await _classify_intent(state)
        logger.info(
            "Router decision: agents=%s, mode=%s, reasoning=%s",
            routing_decision["agents"],
            routing_decision["execution_mode"],
            routing_decision["reasoning"],
        )

    return {
        "routing_decision": routing_decision,
        "pending_agents": routing_decision["agents"][:],
    }


async def _classify_intent(state: DivaState) -> RoutingDecision:
    """Use the LLM to classify intent and select agents."""
    if _registry is None:
        logger.warning("Router registry not configured, falling back to neo4j")
        return _fallback_decision()

    agent_descriptions = _registry.agent_descriptions_for_router()
    max_agents = _registry.execution_config.max_agents_per_query

    system_prompt = _ROUTER_SYSTEM_PROMPT.format(
        agent_descriptions=agent_descriptions,
        max_agents=max_agents,
    )

    llm = get_llm(temperature=0, streaming=False)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["user_message"]),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw = response.content.strip()

        # Strip <think>...</think> tags (Qwen3, DeepSeek, etc.)
        from diva.llm.provider import strip_think_tags
        raw = strip_think_tags(raw)

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3].strip()

        # Try to extract JSON from the response if there's surrounding text
        json_match = re.search(r'\{[^{}]*"agents"[^{}]*\}', raw, re.DOTALL)
        if json_match:
            raw = json_match.group()

        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("Router LLM returned invalid response, falling back to neo4j: %s", exc)
        return _fallback_decision()

    # Validate and clamp
    valid_ids = set(_registry.agent_ids)
    agents = [a for a in parsed.get("agents", []) if a in valid_ids]
    if not agents:
        logger.warning("Router returned no valid agents, falling back to neo4j")
        return _fallback_decision()

    agents = agents[:max_agents]

    execution_mode = parsed.get("execution_mode", "parallel")
    if execution_mode not in ("parallel", "sequential"):
        execution_mode = "parallel"

    sequential_plan = None
    if execution_mode == "sequential" and parsed.get("sequential_plan"):
        sequential_plan = parsed["sequential_plan"]

    return RoutingDecision(
        agents=agents,
        execution_mode=execution_mode,
        reasoning=parsed.get("reasoning", ""),
        sequential_plan=sequential_plan,
    )


def _fallback_decision() -> RoutingDecision:
    """Default routing when LLM classification fails."""
    return RoutingDecision(
        agents=["diva"],
        execution_mode="parallel",
        reasoning="Fallback: routing to DIVA general assistant",
        sequential_plan=None,
    )
