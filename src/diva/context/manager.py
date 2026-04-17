"""Context manager — orchestrates the full context pipeline for each turn."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from diva.context.drift_detector import DriftDetector, DriftSeverity
from diva.context.entity_scratchpad import EntityScratchpad
from diva.context.sliding_window import SlidingWindow
from diva.context.summarizer import RunningSummarizer
from diva.context.token_budget import TokenBudgetAllocator

logger = logging.getLogger(__name__)


class ContextManager:
    """Runs the full context pipeline for every incoming user turn.

    Pipeline steps:
      1. Drift detection
      2. Update sliding window
      3. Update running summary (if window is full / trimmed)
      4. Update entity scratchpad
      5. Allocate token budget
      6. Return assembled context dict
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._config = cfg

        sw_cfg = cfg.get("sliding_window", {})
        self._max_messages = sw_cfg.get("max_turns", 8) * 2  # turns -> messages

        summ_cfg = cfg.get("summarizer", {})
        self._summarize_trigger = summ_cfg.get("trigger_after_turns", 6)

        ent_cfg = cfg.get("entity_scratchpad", {})
        self._entity_ttl = ent_cfg.get("entity_ttl_turns", 12)
        self._max_entities = ent_cfg.get("max_entities", 50)

        drift_cfg = cfg.get("drift_detection", {})
        self._drift_enabled = drift_cfg.get("enabled", True)
        self._drift_min_turns = drift_cfg.get("min_turns_before_check", 2)

        self._drift_detector = DriftDetector()
        self._summarizer = RunningSummarizer()
        self._budget_allocator = TokenBudgetAllocator(cfg)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ContextManager:
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        return cls(config)

    async def process(
        self,
        session_id: str,
        user_message: str,
        session_data: dict,
    ) -> dict:
        """Run the full context pipeline and return the assembled state updates.

        Parameters
        ----------
        session_id:
            Current session identifier.
        user_message:
            The new user message for this turn.
        session_data:
            Session document loaded from MongoDB.

        Returns
        -------
        dict with keys matching DivaState context fields.
        """
        turn_number = session_data.get("turn_count", 0) + 1
        last_user_message = session_data.get("last_user_message", "")
        running_summary = session_data.get("running_summary", "")

        # Rebuild objects from persisted session data
        window = SlidingWindow(session_data.get("sliding_window", []))
        scratchpad = EntityScratchpad(session_data.get("entity_scratchpad", []))

        # ── 1. Drift detection ──────────────────────────────────────────
        drift_detected = False
        drift_reason = ""
        previous_topic_summary = ""

        if (
            self._drift_enabled
            and turn_number > self._drift_min_turns
            and last_user_message
        ):
            drift_result = await self._drift_detector.detect(
                running_summary, last_user_message, user_message,
                active_entities=scratchpad.get_active(),
            )

            if drift_result.severity == DriftSeverity.HARD:
                # Full context reset — completely unrelated topic
                drift_detected = True
                drift_reason = drift_result.reason
                logger.info("HARD drift (turn %d): %s", turn_number, drift_reason)
                previous_topic_summary = running_summary
                running_summary = ""
                scratchpad.tag_stale()
            elif drift_result.severity == DriftSeverity.SOFT:
                # Soft drift — keep summary and entities, just note it
                drift_reason = drift_result.reason
                logger.info("Soft drift (turn %d): %s — keeping context", turn_number, drift_reason)

        # ── 2. Update sliding window ────────────────────────────────────
        window.append("user", user_message, turn_number)
        evicted = window.trim(self._max_messages)

        # ── 3. Update running summary (if turns were evicted) ───────────
        if evicted:
            running_summary = await self._summarizer.compress(
                running_summary, evicted,
            )

        # ── 4. Update entity scratchpad ─────────────────────────────────
        new_entities = await scratchpad.extract_entities(
            user_message, turn_number, source="user",
        )
        scratchpad.update(new_entities, turn_number)
        scratchpad.evict(turn_number, ttl=self._entity_ttl)

        # ── 5. Allocate token budget ────────────────────────────────────
        entities_text = "\n".join(
            f"- {e['name']} ({e['type']})" for e in scratchpad.get_active()
        )
        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in window.get_messages()
        )

        budget = self._budget_allocator.allocate(
            summary=running_summary,
            entities=entities_text,
            history=history_text,
        )

        # ── 6. Assemble context ─────────────────────────────────────────
        return {
            "turn_number": turn_number,
            "sliding_window": window.get_messages(),
            "running_summary": running_summary,
            "entity_scratchpad": scratchpad.get_all(),
            "drift_detected": drift_detected,
            "previous_topic_summary": previous_topic_summary,
            # Expose budget result for downstream nodes
            "_token_budget": budget,
            # Data to persist back to session
            "_session_update": {
                "turn_count": turn_number,
                "sliding_window": window.get_messages(),
                "running_summary": running_summary,
                "entity_scratchpad": scratchpad.get_all(),
                "last_user_message": user_message,
            },
        }
