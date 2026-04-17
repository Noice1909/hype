"""Entity scratchpad — extracts and tracks named entities across conversation turns."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from diva.graph.state import EntityEntry
from diva.llm.provider import get_llm

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = (
    "Extract named entities from the following text. "
    "Entity types: application, table, domain, person, job, platform, service, database.\n"
    "Return a JSON array of objects with keys: name, type.\n"
    "If no entities found, return an empty array [].\n"
    "Output ONLY valid JSON — no markdown fences, no explanation."
)


class EntityScratchpad:
    """Tracks entities mentioned during the conversation with TTL-based eviction."""

    def __init__(
        self,
        entities: list[EntityEntry] | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._entities: list[EntityEntry] = list(entities) if entities else []
        self._model = model

    # ── Extraction ──────────────────────────────────────────────────────

    async def extract_entities(
        self,
        text: str,
        turn_number: int,
        source: str = "user",
    ) -> list[EntityEntry]:
        """Use an LLM to extract entities from *text*.

        Returns a list of new EntityEntry dicts (not yet merged into the pad).
        """
        llm = get_llm(model=self._model, temperature=0, streaming=False, max_tokens=512)
        response = await llm.ainvoke([
            SystemMessage(content=_EXTRACTION_PROMPT),
            HumanMessage(content=text),
        ])

        raw = response.content.strip()
        # Strip <think>...</think> and markdown fences
        from diva.llm.provider import strip_think_tags
        raw = strip_think_tags(raw)
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            # Extract JSON array if embedded in text
            import re
            arr_match = re.search(r'\[.*\]', raw, re.DOTALL)
            if arr_match:
                raw = arr_match.group()
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Entity extraction returned invalid JSON: %s", raw[:200])
            return []

        if not isinstance(parsed, list):
            logger.warning("Entity extraction did not return a list")
            return []

        entries: list[EntityEntry] = []
        for item in parsed:
            if not isinstance(item, dict) or "name" not in item:
                continue
            entries.append(EntityEntry(
                name=item["name"],
                type=item.get("type", "unknown"),
                source=source,
                first_seen_turn=turn_number,
                last_seen_turn=turn_number,
            ))
        return entries

    # ── Update / merge ──────────────────────────────────────────────────

    def update(self, new_entities: list[EntityEntry], turn_number: int) -> None:
        """Merge *new_entities* into the scratchpad.

        If an entity with the same name already exists, refresh its
        ``last_seen_turn``; otherwise append it.
        """
        existing_by_name = {e["name"].lower(): e for e in self._entities}
        for ent in new_entities:
            key = ent["name"].lower()
            if key in existing_by_name:
                existing_by_name[key]["last_seen_turn"] = turn_number
            else:
                self._entities.append(ent)
                existing_by_name[key] = ent

    # ── Eviction ────────────────────────────────────────────────────────

    def evict(self, current_turn: int, ttl: int = 12) -> list[EntityEntry]:
        """Remove entities not seen in the last *ttl* turns. Returns evicted."""
        keep, evicted = [], []
        for e in self._entities:
            if current_turn - e["last_seen_turn"] > ttl:
                evicted.append(e)
            else:
                keep.append(e)
        self._entities = keep
        if evicted:
            logger.debug("Evicted %d stale entities", len(evicted))
        return evicted

    def tag_stale(self) -> None:
        """Mark all current entities as stale (used on topic drift).

        Sets ``last_seen_turn`` to 0 so they will be evicted on next pass.
        """
        for e in self._entities:
            e["last_seen_turn"] = 0

    # ── Accessors ───────────────────────────────────────────────────────

    def get_active(self) -> list[EntityEntry]:
        """Return a copy of all non-stale entities."""
        return [e for e in self._entities if e["last_seen_turn"] > 0]

    def get_all(self) -> list[EntityEntry]:
        """Return a copy of the full entity list."""
        return list(self._entities)

    def __len__(self) -> int:
        return len(self._entities)
