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
    def from_yaml(
        cls,
        path: str | Path,
        enabled_override: set[str] | None = None,
    ) -> AgentRegistry:
        """Load agents.yaml and apply the enable/disable resolution rule.

        Resolution:
          - ``enabled_override`` is None  →  YAML's ``enabled`` flags win.
          - ``enabled_override`` is a set →  exhaustive allowlist; YAML's
            ``enabled`` field is ignored. Unknown ids are warned and dropped.

        The set of agent ids actually registered is what every downstream
        component sees (router prompt, synthesizer suggestion validation,
        agent_executor lookup).
        """
        with open(path) as f:
            raw = yaml.safe_load(f)
        config = AgentRegistryConfig(**raw)
        all_ids = set(config.agents.keys())

        if enabled_override is None:
            kept = {aid for aid, cfg in config.agents.items() if cfg.enabled}
            source = "agents.yaml `enabled` flags"
        else:
            unknown = enabled_override - all_ids
            if unknown:
                logger.warning(
                    "DIVA_ENABLED_AGENTS contains unknown agent ids %s — "
                    "ignored. Known ids: %s", sorted(unknown), sorted(all_ids),
                )
            kept = enabled_override & all_ids
            source = "DIVA_ENABLED_AGENTS env var"

        config.agents = {aid: config.agents[aid] for aid in kept}
        logger.info(
            "Agent registry: %d enabled (source: %s) — %s",
            len(config.agents), source, sorted(config.agents.keys()),
        )
        return cls(config)

    def mcp_servers_needed(self) -> list[str]:
        """Distinct MCP server ids referenced by enabled agents.

        ``mcp_server: "none"`` (the diva persona) is excluded since it has
        no MCP backend.
        """
        return sorted({
            cfg.mcp_server for cfg in self._config.agents.values()
            if cfg.mcp_server and cfg.mcp_server != "none"
        })

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
