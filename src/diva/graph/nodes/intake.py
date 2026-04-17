"""Intake node — loads session state and assembles context.

Phase 3: Full context pipeline with drift detection, running summary,
entity scratchpad, and token budgets via ContextManager.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from diva.context.manager import ContextManager
from diva.graph.state import DivaState
from diva.storage.mongo import load_session, save_session

logger = logging.getLogger(__name__)

# Module-level context manager instance, configured at startup.
_context_manager: ContextManager | None = None


def configure_intake(context_config_path: str) -> None:
    """Initialize the ContextManager from a YAML config file.

    Called once during app startup (from ``main.py``).
    """
    global _context_manager
    _context_manager = ContextManager.from_yaml(context_config_path)
    logger.info("Intake node configured with context pipeline from %s", context_config_path)


async def intake_node(state: DivaState) -> dict:
    """Load session context from MongoDB and run the full context pipeline.

    If ``configure_intake`` has not been called, falls back to a default
    ContextManager with built-in defaults.
    """
    global _context_manager

    session_id = state["session_id"]
    user_message = state["user_message"]

    # Load existing session or start fresh
    session = await load_session(session_id)
    if session is None:
        session = {
            "turn_count": 0,
            "sliding_window": [],
            "running_summary": "",
            "entity_scratchpad": [],
            "last_user_message": "",
        }

    # Use the configured context manager (or a default one)
    if _context_manager is None:
        logger.warning("ContextManager not configured — using defaults")
        _context_manager = ContextManager()

    # Run the full context pipeline
    result = await _context_manager.process(session_id, user_message, session)

    # Persist updated session data to MongoDB
    session_update = result.pop("_session_update", {})
    await save_session(session_id, session_update)

    # Remove internal keys not needed by the graph state
    result.pop("_token_budget", None)

    return result
