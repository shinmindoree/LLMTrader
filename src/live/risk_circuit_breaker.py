"""Non-custodial triple circuit breaker with kill-switch.

Monitors live trading sessions for anomalous conditions and triggers an
immediate kill-switch that severs all exchange connections and wipes
sensitive key material from memory.

Three independent trip conditions (any one triggers the kill-switch):
  1. API order rate exceeds threshold within a sliding window (rate-limit).
  2. Account balance drops more than a configured percentage from session start.
  3. A single order's margin allocation exceeds the maximum allowed amount.

Kill-switch actions:
  - Close all active WebSocket / HTTP connections.
  - Overwrite decrypted API secret key in memory with None.
  - Set the circuit state to OPEN (no further orders allowed).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = auto()   # normal operation
    OPEN = auto()     # tripped — all trading halted


@dataclass
class CircuitBreakerConfig:
    """Configuration for the triple circuit breaker."""

    # Condition 1: Rate-limit protection
    max_orders_per_minute: int = 20
    sliding_window_seconds: int = 60

    # Condition 2: Balance drawdown protection
    max_balance_drawdown_pct: float = 0.05  # 5%

    # Condition 3: Single order margin cap (USDT)
    max_single_order_margin: float = 500.0


class RiskCircuitBreaker:
    """Real-time risk monitor with kill-switch for live Runner containers."""

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        initial_balance: float = 0.0,
    ) -> None:
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._initial_balance: float = initial_balance

        # Sliding window for API order timestamps
        self._order_timestamps: deque[float] = deque()

        # References to live resources that will be severed on kill-switch
        self._ws_connections: list[Any] = []
        self._http_clients: list[Any] = []
        self._api_secret_refs: list[dict[str, Any]] = []

        # Trip reason for diagnostics
        self._trip_reason: str | None = None

    # -- Registration --------------------------------------------------------

    def set_initial_balance(self, balance: float) -> None:
        """Record the session's starting balance for drawdown monitoring."""
        self._initial_balance = balance
        logger.info("Circuit breaker: initial balance set to %.2f", balance)

    def register_ws_connection(self, ws: Any) -> None:
        """Register a WebSocket connection for kill-switch teardown."""
        self._ws_connections.append(ws)

    def register_http_client(self, client: Any) -> None:
        """Register an HTTP client for kill-switch teardown."""
        self._http_clients.append(client)

    def register_api_secret(self, holder: dict[str, Any], key: str = "api_secret") -> None:
        """Register a dict + key pair that holds the decrypted API secret.

        On kill-switch, holder[key] will be overwritten with None.
        """
        self._api_secret_refs.append({"holder": holder, "key": key})

    # -- State queries -------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        """True if the circuit has been tripped (trading halted)."""
        return self._state == CircuitState.OPEN

    @property
    def trip_reason(self) -> str | None:
        return self._trip_reason

    # -- Check methods (called before/after each order) ----------------------

    def record_order_call(self) -> None:
        """Record an outbound order API call timestamp for rate tracking."""
        now = time.monotonic()
        self._order_timestamps.append(now)
        self._prune_old_timestamps(now)

    def check_rate_limit(self) -> bool:
        """Check if order rate exceeds the threshold. Returns True if tripped."""
        now = time.monotonic()
        self._prune_old_timestamps(now)
        count = len(self._order_timestamps)
        if count > self._config.max_orders_per_minute:
            self._trip(
                f"Rate limit exceeded: {count} orders in last "
                f"{self._config.sliding_window_seconds}s "
                f"(limit: {self._config.max_orders_per_minute})"
            )
            return True
        return False

    def check_balance_drawdown(self, current_balance: float) -> bool:
        """Check if balance has dropped beyond the drawdown threshold.

        Returns True if tripped.
        """
        if self._initial_balance <= 0:
            return False
        drawdown = (self._initial_balance - current_balance) / self._initial_balance
        if drawdown >= self._config.max_balance_drawdown_pct:
            self._trip(
                f"Balance drawdown {drawdown:.2%} exceeds limit "
                f"{self._config.max_balance_drawdown_pct:.2%} "
                f"(initial: {self._initial_balance:.2f}, "
                f"current: {current_balance:.2f})"
            )
            return True
        return False

    def check_order_margin(self, margin_amount: float) -> bool:
        """Check if a single order's margin exceeds the cap.

        Returns True if tripped.
        """
        if margin_amount > self._config.max_single_order_margin:
            self._trip(
                f"Single order margin {margin_amount:.2f} USDT exceeds "
                f"limit {self._config.max_single_order_margin:.2f} USDT"
            )
            return True
        return False

    def pre_order_check(
        self,
        current_balance: float,
        order_margin: float,
    ) -> tuple[bool, str]:
        """Composite check to run before every order submission.

        Returns (allowed, reason). If allowed is False, the order MUST NOT
        be sent to the exchange.
        """
        if self.is_open:
            return False, f"Circuit breaker OPEN: {self._trip_reason}"

        # Record this order attempt for rate counting
        self.record_order_call()

        if self.check_rate_limit():
            return False, f"Circuit breaker tripped: {self._trip_reason}"

        if self.check_balance_drawdown(current_balance):
            return False, f"Circuit breaker tripped: {self._trip_reason}"

        if self.check_order_margin(order_margin):
            return False, f"Circuit breaker tripped: {self._trip_reason}"

        return True, "OK"

    # -- Kill-switch ---------------------------------------------------------

    def _trip(self, reason: str) -> None:
        """Trip the circuit breaker and execute the kill-switch."""
        if self._state == CircuitState.OPEN:
            return  # already tripped
        self._state = CircuitState.OPEN
        self._trip_reason = reason
        logger.critical("CIRCUIT BREAKER TRIPPED: %s", reason)
        self.trigger_kill_switch()

    def trigger_kill_switch(self) -> None:
        """Sever all exchange connections and wipe sensitive key material.

        This method is synchronous-safe; it schedules async teardown for
        WebSocket connections if an event loop is running.
        """
        logger.critical("KILL-SWITCH ACTIVATED — severing all connections")

        # 1. Close WebSocket connections
        for ws in self._ws_connections:
            try:
                if hasattr(ws, "close"):
                    close_coro = ws.close()
                    if asyncio.iscoroutine(close_coro):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(close_coro)
                        except RuntimeError:
                            pass  # no event loop; best-effort
            except Exception:
                logger.debug("Error closing WebSocket", exc_info=True)
        self._ws_connections.clear()

        # 2. Close HTTP clients
        for client in self._http_clients:
            try:
                if hasattr(client, "close"):
                    close_coro = client.close()
                    if asyncio.iscoroutine(close_coro):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(close_coro)
                        except RuntimeError:
                            pass
                elif hasattr(client, "session") and hasattr(client.session, "close"):
                    close_coro = client.session.close()
                    if asyncio.iscoroutine(close_coro):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(close_coro)
                        except RuntimeError:
                            pass
            except Exception:
                logger.debug("Error closing HTTP client", exc_info=True)
        self._http_clients.clear()

        # 3. Wipe decrypted API secret keys from memory
        for ref in self._api_secret_refs:
            holder = ref["holder"]
            key = ref["key"]
            try:
                if key in holder:
                    holder[key] = None
                    logger.info("Wiped API secret key '%s' from memory", key)
            except Exception:
                logger.debug("Error wiping key '%s'", key, exc_info=True)
        self._api_secret_refs.clear()

        logger.critical("KILL-SWITCH complete — all connections severed, keys wiped")

    # -- Internal helpers ----------------------------------------------------

    def _prune_old_timestamps(self, now: float) -> None:
        """Remove order timestamps outside the sliding window."""
        cutoff = now - self._config.sliding_window_seconds
        while self._order_timestamps and self._order_timestamps[0] < cutoff:
            self._order_timestamps.popleft()
