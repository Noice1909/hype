"""Custom DeepEval judge that uses DIVA's own LLM provider.

DeepEval ships with native support only for OpenAI/Anthropic/Ollama. For
Tachyon (and to keep one judge class for both Ollama dev + Tachyon prod), we
implement the ``DeepEvalBaseLLM`` interface and proxy to ``get_llm()``.

Usage:
    from diva.evaluation.diva_judge import build_judge
    metric = FaithfulnessMetric(threshold=0.7, model=build_judge())

DeepEval calls ``generate`` / ``a_generate`` once per metric measurement; the
prompt is the metric's internal evaluation prompt and we just return the raw
string. Metric parsing happens in DeepEval itself.
"""

from __future__ import annotations

import logging
from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM
from langchain_core.messages import HumanMessage

from diva.core.config import get_settings
from diva.llm.provider import get_llm, strip_think_tags

logger = logging.getLogger(__name__)


class DivaJudge(DeepEvalBaseLLM):
    """DeepEval judge that talks to DIVA's active LLM provider.

    Stays generic: works with Ollama or Tachyon depending on
    ``settings.llm_provider``. ``model_override`` lets callers pin a specific
    model id for evaluation (e.g. a smaller model than synthesis uses).
    """

    def __init__(
        self,
        model_override: str | None = None,
        provider_override: str | None = None,
        temperature: float = 0,
    ) -> None:
        self._model_override = model_override
        self._provider_override = provider_override
        self._temperature = temperature
        self._llm: Any = None  # lazy

    # ── DeepEvalBaseLLM interface ────────────────────────────────────────────

    def load_model(self) -> Any:
        """Lazy-build the underlying LangChain chat model."""
        if self._llm is None:
            self._llm = get_llm(
                model=self._model_override,
                provider=self._provider_override,
                temperature=self._temperature,
                streaming=False,
            )
        return self._llm

    def get_model_name(self) -> str:
        settings = get_settings()
        provider = (self._provider_override or settings.llm_provider).lower()
        if provider == "tachyon":
            return self._model_override or settings.tachyon_model
        return self._model_override or settings.ollama_model

    def generate(self, prompt: str, *_args: Any, **_kwargs: Any) -> str:
        """Sync evaluation call. DeepEval invokes this on its background thread."""
        llm = self.load_model()
        response = llm.invoke([HumanMessage(content=prompt)])
        return strip_think_tags(getattr(response, "content", "") or "")

    async def a_generate(self, prompt: str, *_args: Any, **_kwargs: Any) -> str:
        """Async evaluation call. Preferred path when DeepEval supports it."""
        llm = self.load_model()
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return strip_think_tags(getattr(response, "content", "") or "")


def build_judge(
    model_override: str | None = None,
    provider_override: str | None = None,
    temperature: float = 0,
) -> DivaJudge:
    """Convenience factory; reads settings.deepeval_model when no override given.

    Resolution:
      - explicit model_override wins
      - else settings.deepeval_model if it doesn't look like an OpenAI default
      - else falls back to the active provider's main model
    """
    settings = get_settings()
    if model_override is None:
        candidate = (settings.deepeval_model or "").strip()
        # gpt-* defaults are unusable without OpenAI key; ignore them and use
        # whatever the active LLM provider already runs.
        if candidate and not candidate.lower().startswith(("gpt-", "openai/")):
            model_override = candidate
    return DivaJudge(
        model_override=model_override,
        provider_override=provider_override,
        temperature=temperature,
    )
