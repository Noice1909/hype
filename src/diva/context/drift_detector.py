"""Drift detector — identifies meaningful topic changes in conversation.

Three-layer detection:
  1. Fast heuristic — entity overlap between turns (no LLM call)
  2. LLM classification — only when heuristic is ambiguous
  3. Severity scoring — soft drift (narrow context) vs hard drift (full reset)

Drift only triggers context reset on HARD drift (completely unrelated topic).
Soft drift preserves the running summary and active entities.
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum

from langchain_core.messages import HumanMessage, SystemMessage

from diva.llm.provider import get_llm, strip_think_tags

logger = logging.getLogger(__name__)


class DriftSeverity(Enum):
    NONE = "none"           # Same topic, no drift
    SOFT = "soft"           # Related topic shift (keep summary, keep entities)
    HARD = "hard"           # Completely unrelated topic (archive + reset)


class DriftResult:
    """Result of drift detection with severity and reason."""
    def __init__(self, severity: DriftSeverity, reason: str):
        self.severity = severity
        self.reason = reason

    @property
    def is_drift(self) -> bool:
        return self.severity == DriftSeverity.HARD

    @property
    def is_soft_drift(self) -> bool:
        return self.severity == DriftSeverity.SOFT

    def __repr__(self):
        return f"DriftResult({self.severity.value}, {self.reason!r})"


# ── Layer 1: Fast Entity Overlap Heuristic ───────────────────────────────────

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "what", "which", "who",
    "how", "many", "much", "do", "does", "did", "can", "could", "would",
    "should", "will", "shall", "may", "might", "have", "has", "had",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "and",
    "or", "not", "no", "but", "if", "then", "than", "that", "this",
    "it", "its", "they", "them", "their", "we", "our", "you", "your",
    "me", "my", "i", "he", "she", "him", "her", "all", "any", "each",
    "some", "show", "give", "tell", "find", "get", "list", "check",
    "about", "please", "also", "there", "here", "be", "been", "being",
})


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text (lowercase, no stop words)."""
    words = set(re.findall(r'\b[a-zA-Z]{3,}\b', text.lower()))
    return words - _STOP_WORDS


def _entity_overlap_score(
    prev_message: str,
    curr_message: str,
    active_entities: list[dict],
) -> float:
    """Score 0.0-1.0 based on keyword/entity overlap between turns.

    1.0 = high overlap (same topic), 0.0 = no overlap (likely drift).
    """
    prev_kw = _extract_keywords(prev_message)
    curr_kw = _extract_keywords(curr_message)

    if not prev_kw or not curr_kw:
        return 0.5  # Can't determine, pass to LLM

    # Direct keyword overlap
    overlap = prev_kw & curr_kw
    kw_score = len(overlap) / min(len(prev_kw), len(curr_kw)) if prev_kw and curr_kw else 0

    # Entity mention score — does current message reference known entities?
    entity_names = {e.get("name", "").lower() for e in active_entities}
    curr_lower = curr_message.lower()
    entity_hits = sum(1 for name in entity_names if name and name in curr_lower)
    entity_score = min(entity_hits / max(len(entity_names), 1), 1.0)

    # Weighted: keyword overlap 60%, entity continuity 40%
    return 0.6 * kw_score + 0.4 * entity_score


# ── Layer 2: LLM Classification (only when heuristic is ambiguous) ──────────

_SYSTEM_PROMPT = """\
You are a topic-drift classifier for an enterprise data exploration chat.

Users naturally explore different aspects of their organization: apps, teams, servers, \
databases, migrations, domains, tickets, jobs. Jumping between these related enterprise \
topics is NORMAL EXPLORATION, not drift.

Drift means the user has COMPLETELY changed context — e.g., from asking about servers \
to asking about the weather, or from database queries to writing a poem.

## Classification:
- "none" — Same topic or natural enterprise exploration (asking about related systems, \
  following up on earlier topics, deepening an investigation)
- "soft" — Shifted to a loosely related area (still enterprise/data but different domain)
- "hard" — Completely unrelated topic (greetings after technical questions, creative \
  requests, geography, personal questions unrelated to data)

Return ONLY valid JSON:
{{"severity": "none"|"soft"|"hard", "reason": "one sentence"}}"""


class DriftDetector:
    """Three-layer drift detection: heuristic → LLM → severity scoring."""

    def __init__(
        self,
        *,
        model: str | None = None,
        heuristic_no_drift_threshold: float = 0.3,
        heuristic_definite_drift_threshold: float = 0.05,
    ) -> None:
        self._model = model
        # Above this overlap score → definitely no drift (skip LLM)
        self._no_drift_threshold = heuristic_no_drift_threshold
        # Below this → definitely hard drift (skip LLM)
        self._definite_drift_threshold = heuristic_definite_drift_threshold

    async def detect(
        self,
        running_summary: str,
        last_user_message: str,
        current_message: str,
        active_entities: list[dict] | None = None,
    ) -> DriftResult:
        """Detect topic drift with severity.

        Returns DriftResult with .is_drift (bool) for backwards compat
        and .severity for fine-grained handling.
        """
        if not last_user_message:
            return DriftResult(DriftSeverity.NONE, "first message")

        # Layer 1: Fast heuristic
        overlap = _entity_overlap_score(
            last_user_message, current_message, active_entities or []
        )

        if overlap >= self._no_drift_threshold:
            logger.debug("Drift heuristic: overlap=%.2f → no drift (skip LLM)", overlap)
            return DriftResult(DriftSeverity.NONE, f"high overlap ({overlap:.2f})")

        if overlap <= self._definite_drift_threshold and not active_entities:
            logger.debug("Drift heuristic: overlap=%.2f → hard drift (skip LLM)", overlap)
            return DriftResult(DriftSeverity.HARD, f"zero overlap ({overlap:.2f})")

        # Layer 2: LLM classification (ambiguous zone)
        return await self._llm_classify(
            running_summary, last_user_message, current_message
        )

    async def _llm_classify(
        self,
        running_summary: str,
        last_user_message: str,
        current_message: str,
    ) -> DriftResult:
        """Use LLM for nuanced drift classification."""
        user_content = (
            f"CONVERSATION SUMMARY:\n{running_summary or '(none)'}\n\n"
            f"PREVIOUS USER MESSAGE:\n{last_user_message}\n\n"
            f"CURRENT USER MESSAGE:\n{current_message}"
        )

        llm = get_llm(model=self._model, temperature=0, streaming=False, max_tokens=256)
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ])

        raw = strip_think_tags(response.content.strip())
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            json_match = re.search(r'\{[^{}]*"severity"[^{}]*\}', raw)
            if json_match:
                raw = json_match.group()
            result = json.loads(raw)

            severity_str = result.get("severity", "none").lower()
            reason = str(result.get("reason", ""))

            severity_map = {
                "none": DriftSeverity.NONE,
                "soft": DriftSeverity.SOFT,
                "hard": DriftSeverity.HARD,
            }
            severity = severity_map.get(severity_str, DriftSeverity.NONE)

        except (json.JSONDecodeError, AttributeError):
            logger.warning("Drift LLM returned invalid JSON: %s", raw[:200])
            severity = DriftSeverity.NONE
            reason = "parse_error — defaulting to no drift"

        logger.info("Drift LLM: severity=%s reason=%s", severity.value, reason)
        return DriftResult(severity, reason)
