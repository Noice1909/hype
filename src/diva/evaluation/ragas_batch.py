"""Offline batch evaluation using Ragas.

Run as CLI:
    python -m diva.evaluation.ragas_batch --session-ids abc-123 def-456
    python -m diva.evaluation.ragas_batch --last-n 100
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


async def run_batch_evaluation(
    session_ids: list[str] | None = None,
    last_n: int | None = None,
) -> dict:
    """Run Ragas batch evaluation on stored conversations.

    Args:
        session_ids: Specific sessions to evaluate.
        last_n: Evaluate the last N messages across all sessions.

    Returns:
        dict with aggregate scores.
    """
    from diva.storage.mongo import get_db, init_mongo

    await init_mongo()
    db = get_db()

    # Fetch messages to evaluate
    query = {}
    if session_ids:
        query["session_id"] = {"$in": session_ids}

    cursor = db.messages.find(
        {**query, "role": "assistant"},
        sort=[("created_at", -1)],
    )
    if last_n:
        cursor = cursor.limit(last_n)

    messages = await cursor.to_list(length=last_n or 1000)

    if not messages:
        logger.info("No messages found for evaluation")
        return {"evaluated": 0}

    try:
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            faithfulness,
        )
        from datasets import Dataset
    except ImportError:
        logger.error("Ragas not installed. Install with: pip install 'diva[eval]'")
        return {"error": "ragas not installed"}

    # Build evaluation dataset
    questions = []
    answers = []
    contexts = []

    for msg in messages:
        # Find corresponding user message
        user_msg = await db.messages.find_one({
            "session_id": msg["session_id"],
            "turn_number": msg["turn_number"],
            "role": "user",
        })
        if not user_msg:
            continue

        questions.append(user_msg["content"])
        answers.append(msg["content"])
        # Use sources as retrieval context proxy
        contexts.append(msg.get("sources", ["No context available"]))

    if not questions:
        return {"evaluated": 0}

    dataset = Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "contexts": contexts,
    })

    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision],
    )

    scores = {
        "evaluated": len(questions),
        "faithfulness": float(result.get("faithfulness", 0)),
        "answer_relevancy": float(result.get("answer_relevancy", 0)),
        "context_precision": float(result.get("context_precision", 0)),
    }

    logger.info("Batch evaluation complete: %s", scores)
    return scores


def main():
    parser = argparse.ArgumentParser(description="DIVA Ragas Batch Evaluation")
    parser.add_argument("--session-ids", nargs="+", help="Session IDs to evaluate")
    parser.add_argument("--last-n", type=int, help="Evaluate last N messages")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run_batch_evaluation(
        session_ids=args.session_ids,
        last_n=args.last_n,
    ))
    print(result)


if __name__ == "__main__":
    main()
