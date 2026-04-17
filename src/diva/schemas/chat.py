"""Request/response schemas — aligned with agent_orchestrator API contract."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    conversation_id: str | None = None
    stream: bool = False
    cypher: str | None = Field(
        default=None,
        description="Optional pre-written Cypher query to execute directly",
    )


class QueryResponse(BaseModel):
    request_id: str
    conversation_id: str
    response: str
    agent: str = ""
    loop_used: str = "langgraph"
    turns_used: int = 0
    duration_ms: float = 0.0
    tools_called: list[str] = Field(default_factory=list)
    cypher_queries: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[dict[str, str]] = Field(default_factory=list)
