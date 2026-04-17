"""Running summarizer — compresses older conversation turns into a rolling summary."""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from diva.llm.provider import get_llm

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a concise conversation summarizer. "
    "Update the existing conversation summary with the new information. "
    "Be concise, keep key entities and decisions. "
    "Output ONLY the updated summary — no preamble, no explanation. "
    "Keep the summary under 300 tokens."
)


class RunningSummarizer:
    """Maintains a running summary of the conversation by compressing older turns."""

    def __init__(self, *, model: str | None = None) -> None:
        self._model = model

    async def compress(
        self,
        existing_summary: str,
        new_turns: list[dict],
    ) -> str:
        """Use an LLM to fold *new_turns* into the *existing_summary*.

        Returns the updated summary string.
        """
        if not new_turns:
            return existing_summary

        turns_text = "\n".join(
            f"{t.get('role', 'unknown')}: {t.get('content', '')}"
            for t in new_turns
        )

        user_content = (
            f"EXISTING SUMMARY:\n{existing_summary or '(none yet)'}\n\n"
            f"NEW TURNS:\n{turns_text}\n\n"
            "Produce the updated summary."
        )

        llm = get_llm(model=self._model, temperature=0, streaming=False, max_tokens=512)
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ])

        summary = response.content.strip()
        logger.debug("Summary updated (%d chars)", len(summary))
        return summary
