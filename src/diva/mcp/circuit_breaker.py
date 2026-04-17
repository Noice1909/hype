"""Circuit breaker for MCP server connections.

Prevents cascading failures when an MCP server is down.
After N consecutive failures, the circuit opens and requests
fail fast for a cooldown period before retrying.
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """Per-server circuit breaker."""

    def __init__(
        self,
        server_id: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ):
        self.server_id = server_id
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit %s: OPEN → HALF_OPEN (cooldown elapsed)", self.server_id)
        return self._state

    def is_available(self) -> bool:
        """Check if the server should accept requests."""
        return self.state != CircuitState.OPEN

    def record_success(self) -> None:
        """Record a successful call — reset the circuit."""
        if self._state != CircuitState.CLOSED:
            logger.info("Circuit %s: %s → CLOSED (success)", self.server_id, self._state.value)
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call — may trip the circuit."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit %s: OPEN (failures=%d, cooldown=%.0fs)",
                self.server_id,
                self._failure_count,
                self.cooldown_seconds,
            )


class CircuitBreakerRegistry:
    """Manages circuit breakers for all MCP servers."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds

    def get(self, server_id: str) -> CircuitBreaker:
        if server_id not in self._breakers:
            self._breakers[server_id] = CircuitBreaker(
                server_id,
                self._failure_threshold,
                self._cooldown_seconds,
            )
        return self._breakers[server_id]

    def all_states(self) -> dict[str, str]:
        return {sid: cb.state.value for sid, cb in self._breakers.items()}
