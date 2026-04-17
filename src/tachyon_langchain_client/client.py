"""TachyonLangchainClient — LangChain ChatOpenAI subclass for the Tachyon API.

Usage
-----
Set the following environment variables before importing:

    TACHYON_BASE_URL        https://apigw-uat.wellsfargo.net/.../tachyon-generation/v1/
    TACHYON_API_KEY         <your api key>
    TACHYON_APIGEE_URL      https://apiidp-nonprod.wellsfargo.net/oauth/token
    TACHYON_CONSUMER_KEY    <apigee consumer key>
    TACHYON_CONSUMER_SECRET <apigee consumer secret>
    TACHYON_USE_CASE_ID     <your use-case id>
    TACHYON_CERTS_PATH      /path/to/WFBTrust.pem  (optional)
    TACHYON_USE_API_GATEWAY TRUE  (set FALSE to skip real auth — dev only)

Then instantiate exactly like ChatOpenAI:

    llm = TachyonLangchainClient(model="gemini-2.0-flash-001", temperature=0)

All APIGEE auth, token caching, and per-request WF headers are handled
internally.  extra_body / reasoning_effort kwargs are forwarded untouched so
that the thinking-model workaround in provider.py works correctly.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import ssl
from collections.abc import AsyncIterator
from typing import Any

import httpx
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr
from typing_extensions import override  # noqa: UP035

from .auth import ApigeeTokenManager
from .config import TachyonConfig
from .headers import HeaderBuilder

logger = logging.getLogger(__name__)

# ── Per-request header injection via ContextVar ────────────────────────────────
# Each asyncio coroutine gets its own context copy, so headers set in one
# request never bleed into another — fixing the shared _custom_headers race.

_tachyon_headers: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "_tachyon_headers", default={},
)


async def _inject_headers_async(request: httpx.Request) -> None:
    """httpx async event hook — must be async per httpx's AsyncClient API."""
    headers = _tachyon_headers.get()
    if headers:
        request.headers.update(headers)
    await asyncio.sleep(0)  # yield to event loop; httpx requires async hook


def _inject_headers_sync(request: httpx.Request) -> None:
    """httpx sync event hook: inject per-request Tachyon/APIGEE headers."""
    headers = _tachyon_headers.get()
    if headers:
        request.headers.update(headers)


def _build_ssl_context(certs_path: str) -> ssl.SSLContext | bool:
    """Return an SSLContext pinned to the WF cert bundle, or True for default verify."""
    if certs_path:
        ctx = ssl.create_default_context(cafile=certs_path)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx
    return True


class TachyonLangchainClient(ChatOpenAI):
    """ChatOpenAI subclass that transparently handles Tachyon / APIGEE auth.

    Config is read from TACHYON_* environment variables at construction time.
    No env-var reading happens at request time — everything is cached.

    All request kwargs (including extra_body, reasoning_effort) are forwarded
    to the parent _agenerate / _generate unchanged.
    """

    _header_builder: HeaderBuilder = PrivateAttr()

    def __init__(self, **kwargs: Any) -> None:
        # ── 1. Load and validate Tachyon config from env ──────────────────────
        config = TachyonConfig.from_env()
        config.validate()

        # ── 2. Build SSL-aware httpx clients with concurrency-safe headers ────
        ssl_ctx = _build_ssl_context(config.certs_path)
        pool_limits = httpx.Limits(
            max_connections=200,
            max_keepalive_connections=80,
        )
        kwargs.setdefault("http_client", httpx.Client(
            verify=ssl_ctx,
            limits=pool_limits,
            event_hooks={"request": [_inject_headers_sync]},
        ))
        kwargs.setdefault("http_async_client", httpx.AsyncClient(
            verify=ssl_ctx,
            limits=pool_limits,
            event_hooks={"request": [_inject_headers_async]},
        ))

        # ── 3. Set base_url and a placeholder api_key ─────────────────────────
        #   base_url: prefer explicit kwarg (allows override), else use config
        kwargs.setdefault("base_url", config.base_url.rstrip("/"))
        #   openai_api_key is required by the OpenAI SDK but auth is handled by
        #   our header injection — the placeholder is never sent to Tachyon.
        kwargs.setdefault("openai_api_key", "tachyon-auth")

        # ── 4. Initialise ChatOpenAI ───────────────────────────────────────────
        super().__init__(**kwargs)

        # ── 5. Build auth + header pipeline ───────────────────────────────────
        token_mgr = ApigeeTokenManager(config)
        self._header_builder = HeaderBuilder(token_mgr, config)

        logger.info(
            "TachyonLangchainClient ready (model=%s, gateway=%s)",
            kwargs.get("model", "unknown"),
            config.use_api_gateway,
        )

    # ── Async path (primary) ─────────────────────────────────────────────────

    @override
    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        headers = await self._header_builder.build()
        ctx_token = _tachyon_headers.set(headers)
        try:
            return await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        finally:
            _tachyon_headers.reset(ctx_token)

    # ── Async streaming path ──────────────────────────────────────────────────

    @override
    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        headers = await self._header_builder.build()
        ctx_token = _tachyon_headers.set(headers)
        try:
            async for chunk in super()._astream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            ):
                yield chunk
        finally:
            _tachyon_headers.reset(ctx_token)

    # ── Sync path (fallback) ─────────────────────────────────────────────────

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Build headers synchronously — _inject_headers_sync event hook
        # reads them from the ContextVar at request time.
        loop = asyncio.new_event_loop()
        try:
            headers = loop.run_until_complete(self._header_builder.build())
        finally:
            loop.close()

        ctx_token = _tachyon_headers.set(headers)
        try:
            return super()._generate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        finally:
            _tachyon_headers.reset(ctx_token)
