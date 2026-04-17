"""Token budget allocator — fits context components within the model's context window."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate *text* so its estimated token count stays within *max_tokens*."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


class TokenBudgetAllocator:
    """Allocates and enforces token budgets for each context component.

    Loads its configuration from a YAML file (``configs/context.yaml``).
    """

    def __init__(self, config: dict | None = None) -> None:
        if config is None:
            config = {}
        budget_cfg = config.get("token_budget", {})
        self._allocations: dict[str, int] = budget_cfg.get("allocations", {
            "system_prompt": 2000,
            "running_summary": 1000,
            "entity_scratchpad": 500,
            "kg_context": 3000,
            "conversation_history": 6000,
            "agent_system_prompt": 1500,
        })
        self._priority_order: list[str] = budget_cfg.get("priority_order", [
            "system_prompt",
            "entity_scratchpad",
            "running_summary",
            "conversation_history",
            "kg_context",
        ])
        self._total = budget_cfg.get("total_context_window", 128_000)
        self._reserved_output = budget_cfg.get("reserved_for_output", 4096)

    @classmethod
    def from_yaml(cls, path: str | Path) -> TokenBudgetAllocator:
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        return cls(config)

    def allocate(
        self,
        system_prompt: str = "",
        summary: str = "",
        entities: str = "",
        history: str = "",
        kg_context: str = "",
    ) -> dict[str, str]:
        """Return truncated versions of each component that fit within budget.

        Components are truncated in reverse priority order — highest-priority
        items are preserved first.
        """
        components = {
            "system_prompt": system_prompt,
            "running_summary": summary,
            "entity_scratchpad": entities,
            "conversation_history": history,
            "kg_context": kg_context,
        }

        result: dict[str, str] = {}
        used = 0
        available = self._total - self._reserved_output

        # Allocate in priority order
        for key in self._priority_order:
            text = components.get(key, "")
            if not text:
                result[key] = ""
                continue

            budget = self._allocations.get(key, 1000)
            # Don't exceed remaining available tokens either
            effective_budget = min(budget, available - used)
            if effective_budget <= 0:
                result[key] = ""
                logger.debug("No budget left for %s", key)
                continue

            truncated = _truncate_to_tokens(text, effective_budget)
            result[key] = truncated
            used += _estimate_tokens(truncated)

        # Include any components not in the priority list
        for key, text in components.items():
            if key not in result:
                result[key] = _truncate_to_tokens(text, self._allocations.get(key, 500))

        return result
