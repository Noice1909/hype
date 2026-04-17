"""MCP client manager — manages connections to MCP servers (stdio/SSE/HTTP).

For HTTP transport, uses direct JSON-RPC over HTTP instead of the MCP SDK's
streamable_http_client, which has anyio task group issues on Windows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


def _resolve_env(value: str) -> str:
    """Replace ${VAR} placeholders with values from Settings.

    Falls back to ``os.environ`` if the var is not in our Settings model
    (e.g., for arbitrary keys passed via OCP ConfigMap that aren't
    declared in the pydantic Settings class).
    """
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        var_name = value[2:-1]
        from diva.core.config import get_settings
        settings_env = get_settings().mcp_server_env()
        if var_name in settings_env:
            return settings_env[var_name]
        return os.environ.get(var_name, "")
    return value


def _resolve_env_dict(env: dict[str, str]) -> dict[str, str]:
    return {k: _resolve_env(v) for k, v in env.items()}


# ── Direct HTTP MCP client (bypasses SDK's anyio issues) ───────��────────────

# Status codes that signal an expired/missing session — triggers reconnect
_SESSION_EXPIRED_STATUSES = frozenset({400, 401, 404, 410})

# Substrings in server error messages that indicate session expiry
_SESSION_EXPIRED_MARKERS = (
    "missing session id",
    "invalid session",
    "session expired",
    "session not found",
    "unknown session",
)


@dataclass
class _HTTPMCPSession:
    """MCP session over streamable HTTP (JSON-RPC) with auto-reconnect.

    - Detects expired sessions (400/401/404/410 + known server messages)
    - Auto-reconnects and retries the failed call once
    - Coordinated via an ``asyncio.Lock`` so concurrent requests don't race
      to reinitialize
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
    _request_id: int = 0
    _client: httpx.AsyncClient | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def initialize(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        # Clear any prior session ID so the server issues a fresh one
        self.session_id = None
        result = await self._rpc("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "diva", "version": "0.1.0"},
        }, _retry_on_session_expired=False)
        logger.debug("MCP initialize result: %s, session_id: %s", result, self.session_id)
        if result:
            logger.info("HTTP MCP initialized: %s (session=%s)", self.url, self.session_id)
        await self._notify("notifications/initialized", {})

    async def reinitialize(self) -> None:
        """Drop the current session and re-handshake. Called on session expiry."""
        async with self._lock:
            # Another coroutine may have already reconnected — check session age
            # by tracking a reconnect generation counter is overkill; we simply
            # re-init unconditionally here, which is idempotent enough.
            logger.info("MCP session expired, reinitializing: %s", self.url)
            await self.initialize()

    async def list_tools(self):
        return await self._rpc("tools/list", {})

    async def call_tool(self, name: str, arguments: dict):
        return await self._rpc("tools/call", {"name": name, "arguments": arguments})

    async def ping(self) -> bool:
        """Send a lightweight request to keep the session alive.

        Returns True if the session is healthy, False otherwise.
        Used by the keepalive task in MCPClientManager.
        """
        try:
            result = await self._rpc("ping", {}, _retry_on_session_expired=True)
            return result is not None
        except Exception:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _rpc(
        self,
        method: str,
        params: dict,
        *,
        _retry_on_session_expired: bool = True,
    ) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        headers = {
            **self.headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        resp = await self._client.post(self.url, json=payload, headers=headers)

        # Extract (possibly-new) session ID from response headers
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid

        # ── Session-expired detection + one-shot reconnect-and-retry ──
        if (
            resp.status_code in _SESSION_EXPIRED_STATUSES
            and _retry_on_session_expired
            and self._looks_like_session_expired(resp)
        ):
            logger.warning(
                "MCP session likely expired (status=%d, url=%s) — reconnecting",
                resp.status_code, self.url,
            )
            await self.reinitialize()
            # Retry the original call exactly once. Suppress further retries
            # to avoid infinite recursion if reconnect also fails.
            return await self._rpc(method, params, _retry_on_session_expired=False)

        if resp.status_code != 200:
            logger.warning("MCP HTTP %s returned %d: %s", method, resp.status_code, resp.text[:200])
            return None

        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse_response(resp.text)
        if resp.text:
            body = resp.json()
            if "error" in body:
                logger.warning("MCP RPC error: %s", body["error"])
                return None
            return body.get("result")
        return None

    @staticmethod
    def _looks_like_session_expired(resp: httpx.Response) -> bool:
        """True if the response body indicates the server forgot our session."""
        text = resp.text.lower() if resp.text else ""
        return any(marker in text for marker in _SESSION_EXPIRED_MARKERS)

    def _parse_sse_response(self, text: str) -> Any:
        """Parse SSE text to extract the JSON-RPC result."""
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                data = line[6:]
                try:
                    parsed = json.loads(data)
                    if "result" in parsed:
                        return parsed["result"]
                except json.JSONDecodeError:
                    continue
        return None

    async def _notify(self, method: str, params: dict) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        headers = {**self.headers, "Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            await self._client.post(self.url, json=payload, headers=headers)
        except Exception:
            pass  # Notifications are fire-and-forget


# ── Adapter to make _HTTPMCPSession tool results look like MCP SDK results ──

class _ToolResult:
    """Minimal adapter matching MCP SDK's call_tool return shape."""
    def __init__(self, content):
        self.content = content


class _TextContent:
    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _ToolInfo:
    """Minimal adapter matching MCP SDK's list_tools return shape."""
    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


# ── Main client manager ─────────────────────────────────────────────────────

class MCPClientManager:
    """Manages MCP server connections as a pool.

    Supports stdio, SSE, and streamable-HTTP transports.
    """

    # Keepalive pings every 5 minutes — well under any reasonable idle
    # timeout (typical MCP servers use 15-30 min). Send when there has
    # been no real traffic in this window.
    _KEEPALIVE_INTERVAL_SECONDS = 300

    def __init__(self, config_path: str) -> None:
        with open(config_path) as f:
            self._config = yaml.safe_load(f)
        self._sessions: dict[str, Any] = {}  # ClientSession or _HTTPMCPSession
        self._cleanup_fns: dict[str, Any] = {}
        self._transport_types: dict[str, str] = {}
        self._keepalive_task: asyncio.Task | None = None

    @property
    def server_ids(self) -> list[str]:
        return list(self._config.get("servers", {}).keys())

    async def startup(self, server_ids: list[str] | None = None) -> None:
        servers = self._config.get("servers", {})
        targets = server_ids or list(servers.keys())

        for sid in targets:
            cfg = servers.get(sid)
            if not cfg:
                logger.warning("MCP server %s not found in config", sid)
                continue

            transport = cfg.get("transport", "stdio")
            startup_timeout = cfg.get("startup_timeout", 30)
            try:
                if transport == "stdio":
                    await asyncio.wait_for(
                        self._start_stdio(sid, cfg), timeout=startup_timeout,
                    )
                elif transport in ("http", "streamable-http"):
                    await asyncio.wait_for(
                        self._start_http(sid, cfg), timeout=startup_timeout,
                    )
                elif transport == "sse":
                    await asyncio.wait_for(
                        self._start_sse(sid, cfg), timeout=startup_timeout,
                    )
                else:
                    logger.error("Unknown transport %s for MCP server %s", transport, sid)
            except asyncio.TimeoutError:
                logger.error("MCP server %s startup timed out after %ds", sid, startup_timeout)
            except Exception:
                logger.exception("Failed to start MCP server %s", sid)

        # Launch keepalive task if any HTTP/SSE sessions are active.
        # stdio sessions don't expire on idle — no keepalive needed for them.
        http_servers = [
            sid for sid in self._sessions
            if self._transport_types.get(sid) in ("http", "sse")
        ]
        if http_servers and self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            logger.info(
                "MCP keepalive task started (interval=%ds, servers=%s)",
                self._KEEPALIVE_INTERVAL_SECONDS, http_servers,
            )

    async def _ping_one(self, server_id: str, session: Any) -> None:
        """Ping a single HTTP/SSE session. Swallows exceptions — the auto-
        reconnect path in ``_HTTPMCPSession._rpc`` will handle recovery
        on the next real request if the ping itself fails here."""
        if self._transport_types.get(server_id) not in ("http", "sse"):
            return
        if not isinstance(session, _HTTPMCPSession):
            return
        try:
            alive = await session.ping()
        except Exception:
            logger.warning(
                "MCP keepalive failed for %s — session will auto-reconnect",
                server_id,
            )
            return
        if alive:
            logger.debug("MCP keepalive OK: %s", server_id)
        else:
            logger.info(
                "MCP keepalive ping for %s returned no result — "
                "session will auto-reconnect on next request",
                server_id,
            )

    async def _keepalive_loop(self) -> None:
        """Periodically ping HTTP/SSE sessions to prevent idle expiry."""
        while True:
            # Cancellation here propagates naturally (returns from the loop
            # cleanly via CancelledError bubbling out — the caller expects
            # this shape from an asyncio.create_task keepalive task).
            await asyncio.sleep(self._KEEPALIVE_INTERVAL_SECONDS)
            for sid, session in self._sessions.items():
                await self._ping_one(sid, session)

    # ── stdio ────────────────────────────────────────────────────────────────

    async def _start_stdio(self, server_id: str, cfg: dict) -> None:
        env = _resolve_env_dict(cfg.get("env", {}))
        merged_env = {**os.environ, **env}

        command = cfg["command"]
        if not os.path.isabs(command):
            abs_cmd = os.path.abspath(command)
            if os.path.exists(abs_cmd):
                command = abs_cmd

        params = StdioServerParameters(
            command=command,
            args=[_resolve_env(a) for a in cfg.get("args", [])],
            env=merged_env,
        )

        ctx = stdio_client(params)
        streams = await ctx.__aenter__()
        self._cleanup_fns[server_id] = ctx

        session = ClientSession(*streams)
        await session.initialize()
        self._sessions[server_id] = session
        self._transport_types[server_id] = "stdio"
        logger.info("MCP server %s started (stdio)", server_id)

    # ── HTTP (direct JSON-RPC) ───────────────────────────────────────────────

    async def _start_http(self, server_id: str, cfg: dict) -> None:
        url = _resolve_env(cfg["url"])
        headers = _resolve_env_dict(cfg.get("headers", {}))

        session = _HTTPMCPSession(url=url, headers=headers)
        await session.initialize()
        self._sessions[server_id] = session
        self._transport_types[server_id] = "http"
        logger.info("MCP server %s started (http: %s)", server_id, url)

    # ── SSE (direct JSON-RPC, same as HTTP) ──────────────────────────────────

    async def _start_sse(self, server_id: str, cfg: dict) -> None:
        url = _resolve_env(cfg["url"])
        headers = _resolve_env_dict(cfg.get("headers", {}))

        session = _HTTPMCPSession(url=url, headers=headers)
        await session.initialize()
        self._sessions[server_id] = session
        self._transport_types[server_id] = "sse"
        logger.info("MCP server %s started (sse: %s)", server_id, url)

    # ── common operations ────────────────────────────────────────────────────

    def is_connected(self, server_id: str) -> bool:
        return server_id in self._sessions

    async def list_tools(self, server_id: str) -> list:
        session = self._sessions.get(server_id)
        if not session:
            raise RuntimeError(f"MCP server {server_id} not connected")

        if isinstance(session, _HTTPMCPSession):
            raw = await session.list_tools()
            if not raw or "tools" not in raw:
                return []
            return [
                _ToolInfo(t["name"], t.get("description", ""), t.get("inputSchema", {}))
                for t in raw["tools"]
            ]

        # SDK ClientSession
        result = await session.list_tools()
        return result.tools

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict) -> Any:
        session = self._sessions.get(server_id)
        if not session:
            raise RuntimeError(f"MCP server {server_id} not connected")

        if isinstance(session, _HTTPMCPSession):
            raw = await session.call_tool(tool_name, arguments)
            if not raw:
                return _ToolResult([_TextContent("Error: no response from MCP server")])
            content = raw.get("content", [])
            return _ToolResult([_TextContent(c.get("text", str(c))) for c in content])

        return await session.call_tool(tool_name, arguments)

    async def shutdown(self) -> None:
        # Cancel keepalive task first so it doesn't race with session close
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._keepalive_task = None

        for sid in list(self._sessions.keys()):
            try:
                session = self._sessions.pop(sid, None)
                if isinstance(session, _HTTPMCPSession):
                    await session.close()
                else:
                    ctx = self._cleanup_fns.pop(sid, None)
                    if ctx:
                        await ctx.__aexit__(None, None, None)
                logger.info("MCP server %s closed", sid)
            except Exception:
                logger.exception("Error closing MCP server %s", sid)
