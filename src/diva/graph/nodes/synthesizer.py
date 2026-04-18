"""Synthesizer node — merges agent results into a coherent response with follow-ups.

Follow-up suggestions are constrained to what the agent fleet can actually
answer. The synthesizer is passed the agent registry at startup, and each
suggestion is tagged with a target agent and a type (depth/breadth):

  - depth   — same agent drill-down on the current response
  - breadth — a different agent's angle on the same entities
"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from diva.agents.registry import AgentRegistry
from diva.graph.state import DivaState
from diva.llm.provider import get_llm, strip_think_tags

logger = logging.getLogger(__name__)

# Module-level registry — populated by configure_synthesizer() at startup.
_registry: AgentRegistry | None = None


def configure_synthesizer(registry: AgentRegistry) -> None:
    """Inject the agent registry so suggestions can be constrained to
    what the system can actually answer. Called once during app startup.
    """
    global _registry
    _registry = registry


_SYNTHESIZER_PROMPT_TEMPLATE = """\
You are the DIVA synthesizer. Your job is to combine results from multiple \
data source agents into a single, coherent, well-structured answer for the user.

Rules:
- Synthesize all agent results into one unified response
- Cite which data source provided each piece of information
- If agents returned conflicting information, note the discrepancy
- Be concise but complete

## Output Format
Your response MUST be in **markdown**:
- **bold** for entity names and important values
- Tables (| Col | Col |) for tabular data
- Bullet lists (- item) for enumerations
- Headers (## Section) when the response has multiple parts
- `code blocks` for Cypher / collection names / technical values
- NEVER output plain unformatted text

## Follow-up Suggestions (IMPORTANT)
After your answer, suggest 2-3 follow-up questions the user might ask next.

Each suggestion MUST:
1. Reference at least one specific entity (name, ID, table, app, etc.) from your response — NEVER be generic trivia.
2. Be answerable by exactly one of the agents listed below. If no agent can answer it, do NOT include it.
3. Be tagged with:
   - type: "depth" (drill into the same topic) or "breadth" (pivot to a related angle another agent handles)
   - agent: the agent id from the list below that would answer it

Prefer a mix: at least one "breadth" suggestion that pivots to a different agent, so the user discovers the multi-agent system's range.

## Available Agents
{agent_descriptions}

## Output Format for Suggestions
Output the suggestions as a JSON array on the last line, wrapped in <followups>...</followups>:

<followups>[
  {{"text": "Which applications depend on pg-core-uat?", "type": "depth", "agent": "neo4j"}},
  {{"text": "Any failing pipelines writing to pg-core-uat in the last week?", "type": "breadth", "agent": "dda-agent"}},
  {{"text": "Which team owns redis-cache-prod?", "type": "depth", "agent": "neo4j"}}
]</followups>

If no grounded + answerable follow-ups exist, emit: <followups>[]</followups>
"""


def _build_system_prompt() -> str:
    """Assemble the synthesizer system prompt with current agent registry."""
    if _registry is None:
        agent_descriptions = "(registry not configured — suggestions may be generic)"
    else:
        lines = []
        for aid, cfg in _registry.agents.items():
            # Compact one-line description per agent
            desc = " ".join(cfg.description.split())[:200]
            lines.append(f"- **{aid}** — {desc}")
        agent_descriptions = "\n".join(lines)
    return _SYNTHESIZER_PROMPT_TEMPLATE.format(agent_descriptions=agent_descriptions)


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
        raw_text = strip_think_tags(successful[0]["response_text"])
        final_response, follow_ups = _extract_followups(raw_text)
        return {
            "final_response": final_response,
            "follow_up_suggestions": follow_ups,
            "sources": [],
        }

    if len(successful) == 1:
        agent_context = (
            f"Agent: {successful[0]['agent_id']}\n"
            f"Response:\n{successful[0]['response_text']}"
        )
    else:
        parts = [
            f"--- Agent: {r['agent_id']} ---\n{r['response_text']}"
            for r in successful
        ]
        agent_context = "\n\n".join(parts)

    # Append error notes
    failed = [r for r in agent_results if r["status"] != "success"]
    if failed:
        fail_notes = ", ".join(f"{r['agent_id']}: {r['response_text']}" for r in failed)
        agent_context += f"\n\n[Note: These agents encountered errors: {fail_notes}]"

    llm = get_llm(temperature=0)
    messages = [
        SystemMessage(content=_build_system_prompt()),
        HumanMessage(content=(
            f"User question: {user_message}\n\n"
            f"Agent results:\n{agent_context}"
        )),
    ]

    response = await llm.ainvoke(messages)
    raw_text = strip_think_tags(response.content)
    final_response, follow_ups = _extract_followups(raw_text)

    return {
        "final_response": final_response,
        "follow_up_suggestions": follow_ups,
        "sources": all_sources,
    }


# ── Follow-up parsing ────────────────────────────────────────────────────


_VALID_TYPES = {"depth", "breadth"}


def _extract_followups(text: str) -> tuple[str, list[dict]]:
    """Extract <followups>...</followups> block.

    Supports both legacy shape (list of strings) and new structured shape
    (list of {text, type, agent}). Always returns a list of dicts with
    at minimum a ``text`` field.
    """
    pattern = r"<followups>\s*(\[.*?\])\s*</followups>"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return text, []

    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Follow-ups block was not valid JSON: %s", match.group(1)[:200])
        return text[:match.start()].rstrip(), []

    clean_text = text[:match.start()].rstrip()
    suggestions = _normalize_suggestions(parsed)
    return clean_text, suggestions


def _normalize_suggestions(parsed: list) -> list[dict]:
    """Normalize mixed-shape parsed suggestions into a uniform list of dicts.

    Filters out suggestions whose ``agent`` doesn't match any known agent —
    those are LLM hallucinations that can't be answered.
    """
    valid_agent_ids = set(_registry.agent_ids) if _registry else set()
    out: list[dict] = []
    for item in parsed:
        suggestion = _normalize_one_suggestion(item, valid_agent_ids)
        if suggestion is not None:
            out.append(suggestion)
    return out


def _normalize_one_suggestion(
    item: object, valid_agent_ids: set[str],
) -> dict | None:
    """Convert a single LLM-emitted item into a validated suggestion dict.

    Returns None if the item is malformed or references an unknown agent.
    """
    if isinstance(item, str):
        text = item.strip()
        return {"text": text} if text else None

    if not isinstance(item, dict):
        return None

    text = str(item.get("text") or item.get("question") or "").strip()
    if not text:
        return None

    suggestion: dict = {"text": text}

    item_type = str(item.get("type") or "").lower()
    if item_type in _VALID_TYPES:
        suggestion["type"] = item_type

    agent = item.get("agent") or item.get("suggested_agent")
    if not agent:
        return suggestion

    agent = str(agent).strip()
    if valid_agent_ids and agent not in valid_agent_ids:
        logger.debug("Dropping suggestion with unknown agent %r: %s", agent, text[:80])
        return None

    suggestion["agent"] = agent
    return suggestion
