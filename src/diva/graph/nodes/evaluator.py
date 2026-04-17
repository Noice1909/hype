"""Evaluator node — fire-and-forget DeepEval evaluation.

Spawns a background task that runs DeepEval metrics AFTER the response
has been sent to the user. Never blocks the response stream.
"""

from __future__ import annotations

import asyncio
import logging

from diva.graph.state import DivaState

logger = logging.getLogger(__name__)


async def evaluator_node(state: DivaState) -> dict:
    """Assemble eval payload and fire background evaluation task."""
    payload = {
        "session_id": state["session_id"],
        "turn_number": state.get("turn_number", 0),
        "user_message": state["user_message"],
        "final_response": state.get("final_response", ""),
        "agent_results": state.get("agent_results", []),
        "sources": state.get("sources", []),
    }

    # Fire and forget — do not await
    asyncio.create_task(_evaluate_background(payload))

    return {"eval_payload": payload}


async def _evaluate_background(payload: dict) -> None:
    """Run DeepEval metrics in background. Never raises."""
    try:
        from diva.evaluation.deep_eval_runner import evaluate_response_async
        await evaluate_response_async(payload)
    except Exception:
        logger.exception("Background evaluation failed (non-blocking)")
