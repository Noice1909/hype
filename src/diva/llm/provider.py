"""LLM provider factory — switches between Ollama (local) and Tachyon (prod).

Usage:
    from diva.llm.provider import get_llm
    llm = get_llm()                          # uses settings.llm_provider
    llm = get_llm(model="llama3.1")          # override model
    llm = get_llm(provider="tachyon")        # force provider
"""

from __future__ import annotations

import re
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from diva.core.config import get_settings

logger = logging.getLogger(__name__)

# ── Think-tag stripping (Qwen3, DeepSeek, etc.) ─────────────────────────────
_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    return _THINK_PATTERN.sub("", text).strip()


def get_llm(
    *,
    model: str | None = None,
    temperature: float = 0,
    streaming: bool = True,
    max_tokens: int = 4096,
    provider: str | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Return a LangChain chat model based on the active provider.

    Provider resolution order:
      1. Explicit ``provider`` argument
      2. ``settings.llm_provider`` (from env / .env)
      3. Default: ``"ollama"``
    """
    settings = get_settings()
    active = (provider or settings.llm_provider).lower()

    if active == "ollama":
        return _build_ollama(model, temperature, streaming, **kwargs)

    if active == "tachyon":
        return _build_tachyon(model, temperature, streaming, max_tokens, **kwargs)

    raise ValueError(f"Unknown LLM provider: {active!r}. Expected 'ollama' or 'tachyon'.")


def _build_ollama(
    model: str | None,
    temperature: float,
    streaming: bool,  # noqa: ARG001 — kept for API symmetry with _build_tachyon
    **kwargs: Any,
) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    settings = get_settings()
    resolved_model = model or settings.ollama_model
    logger.info("LLM provider: Ollama (model=%s)", resolved_model)
    return ChatOllama(
        model=resolved_model,
        temperature=temperature,
        base_url=settings.ollama_base_url,
        **kwargs,
    )


def _build_tachyon(
    model: str | None,
    temperature: float,
    streaming: bool,
    max_tokens: int,
    **kwargs: Any,
) -> BaseChatModel:
    from tachyon_langchain_client import TachyonLangchainClient

    settings = get_settings()
    resolved_model = model or settings.tachyon_model
    logger.info("LLM provider: Tachyon (model=%s)", resolved_model)
    return TachyonLangchainClient(
        model=resolved_model,
        temperature=temperature,
        streaming=streaming,
        max_tokens=max_tokens,
        **kwargs,
    )
