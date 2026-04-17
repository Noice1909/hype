"""Middleware for auth, rate limiting, and request tracking."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request for tracing."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(round(elapsed, 1))

        logger.info(
            "req=%s method=%s path=%s status=%d time=%.1fms",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter per client IP.

    For production, replace with Redis-based rate limiting.
    """

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Clean old entries
        window_start = now - self.window_seconds
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > window_start
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            return Response(
                content='{"error": "Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(self.window_seconds)},
            )

        self._requests[client_ip].append(now)
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT/SSO authentication middleware.

    Phase 7 placeholder — validates Bearer tokens.
    In production, this should verify JWT signature against the org's JWKS endpoint.
    """

    def __init__(self, app, enabled: bool = False, public_paths: set[str] | None = None):
        super().__init__(app)
        self.enabled = enabled
        self.public_paths = public_paths or {"/health", "/ready", "/docs", "/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        if request.url.path in self.public_paths:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response(
                content='{"error": "Missing or invalid Authorization header"}',
                status_code=401,
                media_type="application/json",
            )

        token = auth_header[7:]
        # TODO: Validate JWT against org's JWKS endpoint
        # For now, accept any non-empty token
        if not token:
            return Response(
                content='{"error": "Invalid token"}',
                status_code=401,
                media_type="application/json",
            )

        request.state.user_token = token
        return await call_next(request)
