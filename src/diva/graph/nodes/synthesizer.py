"""Synthesizer node — merges agent results into a coherent response with follow-ups."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from diva.graph.state import DivaState
from diva.llm.provider import get_llm

logger = logging.getLogger(__name__)

_SYNTHESIZER_PROMPT = """You are the DIVA synthesizer. Your job is to combine results from multiple \
data source agents into a single, coherent, well-structured answer for the user.

Rules:
- Synthesize all agent results into one unified response
- Cite which data source provided each piece of information
- If agents returned conflicting information, note the discrepancy
- Be concise but complete

## Output Format (IMPORTANT)
Your response MUST be in **markdown** format for proper UI rendering:
- Use **bold** for entity names and important values
- Use tables (| Col | Col |) for any structured/tabular data
- Use bullet lists (- item) for enumerations
- Use headers (## Section) when the response has multiple parts
- Use `code blocks` for technical values like Cypher queries or collection names
- NEVER output plain unformatted text — always use markdown

## Follow-up Suggestions
After your answer, suggest 2-3 follow-up questions the user might ask next.
Ground them in specific entities and data sources mentioned.
Format: <followups>["question1", "question2", "question3"]</followups>
"""


async def synthesizer_node(state: DivaState) -> dict:
    """Merge agent results and generate follow-up suggestions."""
    agent_results = state.get("agent_results", [])
    user_message = state["user_message"]

    # If only one successful agent, pass through directly
    successful = [r for r in agent_results if r["status"] == "success"]
    all_sources = []
    for r in agent_results:
        all_sources.extend(r.get("sources", []))

    if len(successful) == 1 and not successful[0]["response_text"]:
        return {
            "final_response": "I couldn't find relevant information for your question.",
            "follow_up_suggestions": [],
            "sources": all_sources,
        }

    # DIVA agent responses pass through directly — no synthesis wrapping needed
    if len(successful) == 1 and successful[0]["agent_id"] == "diva":
        raw_text = successful[0]["response_text"]
        from diva.llm.provider import strip_think_tags
        raw_text = strip_think_tags(raw_text)
        final_response, follow_ups = _extract_followups(raw_text)
        return {
            "final_response": final_response,
            "follow_up_suggestions": follow_ups,
            "sources": [],
        }

    if len(successful) == 1:
        # Single agent — run through synthesizer for follow-ups
        agent_context = (
            f"Agent: {successful[0]['agent_id']}\n"
            f"Response:\n{successful[0]['response_text']}"
        )
    else:
        # Multiple agents — merge
        parts = []
        for r in successful:
            parts.append(f"--- Agent: {r['agent_id']} ---\n{r['response_text']}")
        agent_context = "\n\n".join(parts)

    # Add error info if any agents failed
    failed = [r for r in agent_results if r["status"] != "success"]
    if failed:
        fail_notes = ", ".join(f"{r['agent_id']}: {r['response_text']}" for r in failed)
        agent_context += f"\n\n[Note: These agents encountered errors: {fail_notes}]"

    llm = get_llm(temperature=0)
    messages = [
        SystemMessage(content=_SYNTHESIZER_PROMPT),
        HumanMessage(content=(
            f"User question: {user_message}\n\n"
            f"Agent results:\n{agent_context}"
        )),
    ]

    response = await llm.ainvoke(messages)
    raw_text = response.content

    # Strip <think>...</think> tags (Qwen3, DeepSeek, etc.)
    from diva.llm.provider import strip_think_tags
    raw_text = strip_think_tags(raw_text)

    # Parse follow-up suggestions
    final_response, follow_ups = _extract_followups(raw_text)

    return {
        "final_response": final_response,
        "follow_up_suggestions": follow_ups,
        "sources": all_sources,
    }


def _extract_followups(text: str) -> tuple[str, list[str]]:
    """Extract <followups> tag from response text."""
    pattern = r"<followups>\s*(\[.*?\])\s*</followups>"
    match = re.search(pattern, text, re.DOTALL)

    if match:
        try:
            follow_ups = json.loads(match.group(1))
            clean_text = text[:match.start()].rstrip()
            return clean_text, follow_ups
        except json.JSONDecodeError:
            pass

    return text, []
