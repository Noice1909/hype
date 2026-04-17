"""Async APIGEE OAuth2 token manager with TTL caching."""

from __future__ import annotations

import asyncio
import base64
import logging

import httpx
from cachetools import TTLCache

from .config import TachyonConfig

logger = logging.getLogger(__name__)

_TOKEN_TTL = 2700  # 45 min (token valid 60 min; refresh 15 min early)


class ApigeeTokenManager:
    """Fetches and caches an APIGEE OAuth2 Bearer token.

    - Token is cached for 45 minutes (TTLCache).
    - asyncio.Lock prevents duplicate concurrent fetches.
    - All network I/O is async (httpx); never blocks the event loop.
    """

    def __init__(self, config: TachyonConfig) -> None:
        self._config = config
        self._cache: TTLCache[str, str] = TTLCache(maxsize=100, ttl=_TOKEN_TTL)
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Return a valid Bearer token, fetching a new one if the cache is empty."""
        if not self._config.gateway_enabled:
            return "dummy"

        # Fast path: cached token available
        if "token" in self._cache:
            return self._cache["token"]

        # Slow path: fetch under lock to prevent stampede
        async with self._lock:
            # Re-check after acquiring lock (another coroutine may have populated it)
            if "token" in self._cache:
                return self._cache["token"]

            token = await self._fetch_token()
            self._cache["token"] = token
            logger.info("APIGEE: new access token fetched and cached")
            return token

    async def _fetch_token(self) -> str:
        creds = f"{self._config.consumer_key}:{self._config.consumer_secret}"
        b64 = base64.b64encode(creds.encode()).decode()

        async with httpx.AsyncClient(verify=False) as client:  # noqa: S501 — corporate network
            resp = await client.post(
                self._config.apigee_url,
                headers={"Authorization": f"Basic {b64}"},
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
