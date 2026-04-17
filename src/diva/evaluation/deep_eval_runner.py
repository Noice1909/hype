"""Async fire-and-forget DeepEval evaluation runner.

Runs DeepEval metrics in background after the response has been sent.
Never blocks the user's response stream. Results are persisted to MongoDB.

DeepEval / telemetry hardening (read-only container safe) is centralised
in ``diva.core.config`` and applied to ``os.environ`` once at app startup
in ``diva.main``. We do NOT re-apply it here because deepeval reads its
env vars at *its own* import time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from diva.core.config import get_settings
from diva.storage.mongo import save_eval_result

logger = logging.getLogger(__name__)


# Ensure the redirected results folder exists (read-only container safety).
# This is a one-shot side effect at module import; the path comes from settings.
_settings = get_settings()
_results_folder = _settings.deepeval_results_folder or tempfile.gettempdir()
try:
    os.makedirs(_results_folder, exist_ok=True)
except OSError:
    # In a strict read-only container the folder may not be creatable;
    # deepeval will fall back to its own internal handling and our save
    # path is MongoDB anyway.
    pass


async def evaluate_response_async(payload: dict) -> None:
    """Run DeepEval metrics in background. Never raises — logs errors.

    This function is called via asyncio.create_task() and runs
    completely independently of the response stream.
    """
    try:
        retrieval_context = [
            r["response_text"]
            for r in payload.get("agent_results", [])
            if r.get("status") == "success" and r.get("response_text")
        ]

        if not retrieval_context:
            logger.info("No retrieval context for evaluation — skipping")
            return

        # Import DeepEval lazily — it's an optional dependency
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            FaithfulnessMetric,
            HallucinationMetric,
        )
        from deepeval.test_case import LLMTestCase

        test_case = LLMTestCase(
            input=payload["user_message"],
            actual_output=payload["final_response"],
            retrieval_context=retrieval_context,
        )

        metrics = [
            FaithfulnessMetric(threshold=0.7),
            AnswerRelevancyMetric(threshold=0.7),
            HallucinationMetric(threshold=0.5),
        ]

        results = {}
        for metric in metrics:
            await asyncio.to_thread(metric.measure, test_case)
            metric_name = metric.__class__.__name__
            results[metric_name] = {
                "score": metric.score,
                "reason": getattr(metric, "reason", ""),
                "passed": metric.is_successful(),
            }
            logger.info(
                "DeepEval %s: score=%.2f passed=%s",
                metric_name,
                metric.score,
                metric.is_successful(),
            )

        # Persist to MongoDB
        await save_eval_result(
            session_id=payload["session_id"],
            turn_number=payload["turn_number"],
            scores={
                "faithfulness": results.get("FaithfulnessMetric", {}).get("score", 0),
                "relevancy": results.get("AnswerRelevancyMetric", {}).get("score", 0),
                "hallucination": results.get("HallucinationMetric", {}).get("score", 0),
                "details": results,
            },
        )

    except ImportError:
        logger.warning(
            "DeepEval not installed — skipping evaluation. "
            "Install with: pip install 'diva[eval]'"
        )
    except Exception:
        logger.exception("DeepEval background evaluation failed (non-blocking)")
