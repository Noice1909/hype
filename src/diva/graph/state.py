"""LangGraph state definition — the single source of truth flowing through the graph."""

from __future__ import annotations

from typing import TypedDict, Literal


class EntityEntry(TypedDict):
    name: str
    type: str  # "application", "table", "domain", "person", "job", etc.
    source: str  # which agent surfaced it
    first_seen_turn: int
    last_seen_turn: int


class AgentResult(TypedDict):
    agent_id: str
    status: Literal["success", "error", "timeout"]
    response_text: str
    tool_calls_made: list[dict]
    tokens_used: int
    latency_ms: float
    sources: list[str]


class RoutingDecision(TypedDict):
    agents: list[str]
    execution_mode: Literal["parallel", "sequential"]
    reasoning: str
    sequential_plan: list[dict] | None


class DivaState(TypedDict):
    # -- Input --
    session_id: str
    user_message: str
    turn_number: int
    # When set, the agent_executor skips the LLM tool-calling loop and
    # runs this Cypher verbatim via the neo4j MCP server. UI "run this
    # query" fast-path — router also short-circuits to neo4j.
    cypher_override: str | None

    # -- Context (assembled by intake_node) --
    running_summary: str
    entity_scratchpad: list[EntityEntry]
    sliding_window: list[dict]  # last N turns as {"role": ..., "content": ...}
    drift_detected: bool
    previous_topic_summary: str

    # -- Routing --
    routing_decision: RoutingDecision

    # -- Agent execution --
    agent_results: list[AgentResult]
    pending_agents: list[str]

    # -- Synthesis --
    final_response: str
    follow_up_suggestions: list[str]
    sources: list[str]

    # -- Eval metadata (not sent to user) --
    eval_payload: dict
