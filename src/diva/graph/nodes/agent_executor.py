"""Agent executor node — runs agents against MCP tools.

This is the most complex node. Each agent:
  1. Gets its config from the registry
  2. Gets MCP tools from its assigned server
  3. Runs a ReAct-style tool-calling loop
  4. Returns structured AgentResult
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from diva.graph.state import AgentResult, DivaState
from diva.agents.registry import AgentRegistry
from diva.mcp.client import MCPClientManager
from diva.mcp.tool_adapter import adapt_mcp_tools

logger = logging.getLogger(__name__)

# These are injected at graph build time via functools.partial or closure
_registry: AgentRegistry | None = None
_mcp_manager: MCPClientManager | None = None
_llm_factory: Any = None


def configure_executor(
    registry: AgentRegistry,
    mcp_manager: MCPClientManager,
    llm_factory: Any,
) -> None:
    """Set module-level dependencies. Called once during app startup."""
    global _registry, _mcp_manager, _llm_factory
    _registry = registry
    _mcp_manager = mcp_manager
    _llm_factory = llm_factory


async def agent_executor_node(state: DivaState) -> dict:
    """Execute all pending agents (parallel via asyncio.gather)."""
    pending = state.get("pending_agents", [])
    if not pending:
        return {"agent_results": state.get("agent_results", []), "pending_agents": []}

    tasks = [_run_single_agent(aid, state) for aid in pending]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    agent_results = list(state.get("agent_results", []))
    for aid, result in zip(pending, results):
        if isinstance(result, Exception):
            logger.exception("Agent %s failed", aid)
            agent_results.append(AgentResult(
                agent_id=aid,
                status="error",
                response_text=f"Agent error: {result}",
                tool_calls_made=[],
                tokens_used=0,
                latency_ms=0,
                sources=[],
            ))
        else:
            agent_results.append(result)

    return {"agent_results": agent_results, "pending_agents": []}


async def _run_single_agent(agent_id: str, state: DivaState) -> AgentResult:
    """Execute a single agent with its MCP tools."""
    start = time.perf_counter()

    config = _registry.get(agent_id)
    if not config:
        return AgentResult(
            agent_id=agent_id,
            status="error",
            response_text=f"Agent {agent_id} not found in registry",
            tool_calls_made=[],
            tokens_used=0,
            latency_ms=0,
            sources=[],
        )

    # Cypher fast-path: if the UI provided a pre-written Cypher query and
    # this is the neo4j agent, skip the LLM tool-calling loop and run
    # the query verbatim. The synthesizer will still produce natural
    # language from the result.
    cypher_override = state.get("cypher_override")
    if cypher_override and agent_id == "neo4j":
        return await _run_cypher_fast_path(
            agent_id, config, cypher_override, start,
        )

    # Get MCP tools for this agent (skip for agents with no MCP server like "diva")
    tools = []
    tool_calls_made = []
    if config.mcp_server != "none" and _mcp_manager.is_connected(config.mcp_server):
        mcp_tools = await _mcp_manager.list_tools(config.mcp_server)
        tools = adapt_mcp_tools(config.mcp_server, mcp_tools, _mcp_manager)
        logger.info("Agent %s has %d MCP tools", agent_id, len(tools))

    # Build the LLM with tools bound
    llm: BaseChatModel = _llm_factory(temperature=config.temperature)
    if tools:
        llm_with_tools = llm.bind_tools(tools)
    else:
        llm_with_tools = llm

    # Build messages — for sequential mode, include prior agent output as context
    system_msg = SystemMessage(content=_build_agent_prompt(agent_id, config, state))
    user_content = state["user_message"]

    routing = state.get("routing_decision", {})
    if routing.get("execution_mode") == "sequential" and routing.get("sequential_plan"):
        prior_context = _get_prior_agent_context(agent_id, routing["sequential_plan"], state)
        if prior_context:
            user_content = (
                f"{user_content}\n\n"
                f"--- Context from previous agent ---\n{prior_context}"
            )

    user_msg = HumanMessage(content=user_content)

    messages = [system_msg]
    # Add relevant conversation history
    for turn in state.get("sliding_window", [])[-6:]:  # last 3 exchanges
        role = turn.get("role", "user")
        if role == "user":
            messages.append(HumanMessage(content=turn["content"]))
        else:
            from langchain_core.messages import AIMessage
            messages.append(AIMessage(content=turn["content"]))
    messages.append(user_msg)

    # ReAct loop: call LLM, execute tools, repeat until done or max calls
    response_text = ""
    sources = []
    for step in range(config.max_tool_calls + 1):
        response = await llm_with_tools.ainvoke(messages)

        # Check for tool calls
        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                logger.info("Agent %s calling tool: %s(%s)", agent_id, tool_name, tool_args)

                # Find and execute the tool
                tool_result = "Tool not found"
                for t in tools:
                    if t.name == tool_name:
                        tool_result = await t._arun(**tool_args)
                        break

                tool_calls_made.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result_preview": str(tool_result)[:200],
                })
                sources.append(f"{config.mcp_server}:{tool_name}")

                from langchain_core.messages import ToolMessage
                messages.append(ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tc["id"],
                ))
        else:
            # No tool calls — final response
            from diva.llm.provider import strip_think_tags
            response_text = strip_think_tags(response.content)
            break

    elapsed_ms = (time.perf_counter() - start) * 1000

    return AgentResult(
        agent_id=agent_id,
        status="success",
        response_text=response_text,
        tool_calls_made=tool_calls_made,
        tokens_used=0,  # TODO: extract from response metadata
        latency_ms=round(elapsed_ms, 1),
        sources=sources,
    )


async def _run_cypher_fast_path(
    agent_id: str,
    config: Any,
    cypher: str,
    start: float,
) -> AgentResult:
    """Execute a user-provided Cypher query directly — no LLM, no ReAct loop.

    Returns an AgentResult shaped exactly like a normal neo4j agent
    response so the synthesizer produces the same natural-language markdown.
    """
    logger.info("Cypher fast-path: running query (%d chars)", len(cypher))

    if not _mcp_manager.is_connected(config.mcp_server):
        elapsed = (time.perf_counter() - start) * 1000
        return AgentResult(
            agent_id=agent_id,
            status="error",
            response_text=f"Neo4j MCP server '{config.mcp_server}' is not connected",
            tool_calls_made=[],
            tokens_used=0,
            latency_ms=round(elapsed, 1),
            sources=[],
        )

    try:
        result = await _mcp_manager.call_tool(
            config.mcp_server, "run_cypher", {"query": cypher},
        )
        result_text = _extract_tool_text(result)
        status = "success"
    except Exception as exc:
        logger.exception("Cypher fast-path execution failed")
        result_text = f"Error executing Cypher: {exc}"
        status = "error"

    elapsed_ms = (time.perf_counter() - start) * 1000
    return AgentResult(
        agent_id=agent_id,
        status=status,
        response_text=result_text,
        tool_calls_made=[{
            "tool": "run_cypher",
            "args": {"query": cypher},
            "result_preview": result_text[:200] if result_text else "",
        }],
        tokens_used=0,
        latency_ms=round(elapsed_ms, 1),
        sources=[f"{config.mcp_server}:run_cypher"],
    )


def _extract_tool_text(result: Any) -> str:
    """Best-effort extraction of text content from an MCP tool result."""
    if hasattr(result, "content"):
        parts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(parts) if parts else str(result.content)
    return str(result)


def _get_prior_agent_context(
    agent_id: str, sequential_plan: list[dict], state: DivaState
) -> str:
    """For sequential execution, find output from the agent that feeds into this one."""
    # Find which agent feeds into the current one
    feeder_id = None
    for step in sequential_plan:
        if step.get("feeds_into") == agent_id:
            feeder_id = step.get("agent")
            break

    if not feeder_id:
        return ""

    # Look up the feeder agent's result
    for result in state.get("agent_results", []):
        if result["agent_id"] == feeder_id and result["status"] == "success":
            return result["response_text"]

    return ""


def _build_agent_prompt(agent_id: str, config: Any, state: DivaState) -> str:
    """Build the system prompt for an agent."""
    summary = state.get("running_summary", "")
    summary_block = f"\n\nConversation context:\n{summary}" if summary else ""

    return (
        f"You are the {config.display_name}, part of the DIVA multi-agent system.\n"
        f"Your role: {config.description}\n"
        f"You have access to tools via MCP. Use them to answer the user's question.\n"
        f"Be concise and cite your data sources.{summary_block}"
    )
