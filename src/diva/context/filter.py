"""Agent context filter — scopes context to what each agent needs."""

from __future__ import annotations

import logging

from diva.graph.state import EntityEntry

logger = logging.getLogger(__name__)


class AgentContextFilter:
    """Filters conversation context to match a specific agent's scope."""

    @staticmethod
    def filter_for_agent(
        agent_config: dict,
        sliding_window: list[dict],
        entities: list[EntityEntry],
        summary: str,
    ) -> dict:
        """Return context filtered to entities and history relevant to *agent_config*.

        Parameters
        ----------
        agent_config:
            Must contain a ``scope`` key with a list of keyword strings.
        sliding_window:
            List of ``{"role", "content", "turn"}`` dicts.
        entities:
            List of EntityEntry dicts.
        summary:
            The running conversation summary.

        Returns
        -------
        dict with keys ``summary``, ``entities``, ``history``.
        """
        scope_keywords = [kw.lower() for kw in agent_config.get("scope", [])]
        if not scope_keywords:
            # No scope defined — pass everything through
            return {
                "summary": summary,
                "entities": entities,
                "history": sliding_window,
            }

        # Filter entities to those whose name or type matches a scope keyword
        filtered_entities = [
            e for e in entities
            if _matches_scope(e, scope_keywords)
        ]

        # Collect entity names for history filtering
        entity_names = {e["name"].lower() for e in filtered_entities}

        # Filter history to turns that mention any matching entity or scope keyword
        match_terms = entity_names | set(scope_keywords)
        filtered_history = [
            msg for msg in sliding_window
            if _message_matches(msg, match_terms)
        ]

        return {
            "summary": summary,
            "entities": filtered_entities,
            "history": filtered_history,
        }


def _matches_scope(entity: EntityEntry, scope_keywords: list[str]) -> bool:
    """Check if an entity's name or type overlaps with any scope keyword."""
    name_lower = entity["name"].lower()
    type_lower = entity["type"].lower()
    for kw in scope_keywords:
        if kw in name_lower or kw in type_lower or name_lower in kw:
            return True
    return False


def _message_matches(message: dict, terms: set[str]) -> bool:
    """Check if a message's content mentions any of the given terms."""
    content_lower = message.get("content", "").lower()
    for term in terms:
        if term in content_lower:
            return True
    return False
