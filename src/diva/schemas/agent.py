"""Agent registry schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    display_name: str
    description: str
    scope: list[str] = Field(default_factory=list)
    mcp_server: str
    prompt_template: str
    temperature: float = 0
    max_tool_calls: int = 5
    enabled: bool = True


class RouterConfig(BaseModel):
    temperature: float = 0
    max_tokens: int = 256


class ExecutionConfig(BaseModel):
    parallel_timeout_seconds: int = 30
    max_agents_per_query: int = 4


class AgentRegistryConfig(BaseModel):
    agents: dict[str, AgentConfig]
    router: RouterConfig = Field(default_factory=RouterConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
