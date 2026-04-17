"""Agent registry — load agent configs from YAML."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from diva.schemas.agent import AgentConfig, AgentRegistryConfig

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Holds all agent configurations and provides lookup."""

    def __init__(self, config: AgentRegistryConfig) -> None:
        self._config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> AgentRegistry:
        with open(path) as f:
            raw = yaml.safe_load(f)
        config = AgentRegistryConfig(**raw)
        logger.info("Loaded %d agents from registry", len(config.agents))
        return cls(config)

    @property
    def agents(self) -> dict[str, AgentConfig]:
        return self._config.agents

    @property
    def agent_ids(self) -> list[str]:
        return list(self._config.agents.keys())

    def get(self, agent_id: str) -> AgentConfig | None:
        return self._config.agents.get(agent_id)

    @property
    def router_config(self):
        return self._config.router

    @property
    def execution_config(self):
        return self._config.execution

    def agent_descriptions_for_router(self) -> str:
        """Format all agent descriptions for the router prompt."""
        lines = []
        for aid, cfg in self._config.agents.items():
            scope_str = ", ".join(cfg.scope)
            lines.append(
                f"- **{aid}** ({cfg.display_name}): {cfg.description}\n"
                f"  Scope keywords: {scope_str}"
            )
        return "\n".join(lines)

    def reload(self, path: str | Path) -> None:
        """Hot-reload the registry from disk."""
        with open(path) as f:
            raw = yaml.safe_load(f)
        self._config = AgentRegistryConfig(**raw)
        logger.info("Reloaded agent registry: %d agents", len(self._config.agents))
