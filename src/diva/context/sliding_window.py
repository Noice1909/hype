"""Sliding window — manages the last N turns of conversation history."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SlidingWindow:
    """Manages a bounded list of recent conversation messages.

    Backed by session data loaded from MongoDB; the caller is responsible
    for persisting the updated window back to the session store.
    """

    def __init__(self, messages: list[dict] | None = None) -> None:
        self._messages: list[dict] = list(messages) if messages else []

    # ── Mutators ────────────────────────────────────────────────────────

    def append(self, role: str, content: str, turn_number: int) -> None:
        """Add a message to the window."""
        self._messages.append({
            "role": role,
            "content": content,
            "turn": turn_number,
        })

    def trim(self, max_messages: int = 16) -> list[dict]:
        """Keep only the last *max_messages* messages (default 16 = 8 pairs).

        Returns the messages that were evicted (useful for summarization).
        """
        if len(self._messages) <= max_messages:
            return []
        evicted = self._messages[:-max_messages]
        self._messages = self._messages[-max_messages:]
        return evicted

    def pop_oldest(self, count: int = 2) -> list[dict]:
        """Remove and return the oldest *count* messages (for summarization)."""
        popped = self._messages[:count]
        self._messages = self._messages[count:]
        return popped

    # ── Accessors ───────────────────────────────────────────────────────

    def get_messages(self) -> list[dict]:
        """Return the current list of messages."""
        return list(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def __bool__(self) -> bool:
        return bool(self._messages)
