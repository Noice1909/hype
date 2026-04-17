"""FastAPI dependency injection."""

from __future__ import annotations

from diva.agents.registry import AgentRegistry
from diva.mcp.client import MCPClientManager
from diva.storage.mongo import get_db

# Module-level references set during app startup
_registry: AgentRegistry | None = None
_mcp_manager: MCPClientManager | None = None
_graph = None


def set_dependencies(registry, mcp_manager, graph):
    global _registry, _mcp_manager, _graph
    _registry = registry
    _mcp_manager = mcp_manager
    _graph = graph


def get_registry() -> AgentRegistry:
    return _registry


def get_mcp_manager() -> MCPClientManager:
    return _mcp_manager


def get_graph():
    return _graph
