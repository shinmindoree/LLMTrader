"""Pre-flight sandbox for LLM-generated strategy code.

Performs a memory-only dry-run of the strategy against recent market data
BEFORE allowing it to connect to a live trading account. This catches
runtime errors (ZeroDivisionError, IndexError, KeyError, etc.) that static
analysis cannot detect, preventing faulty code from reaching production.

Pipeline:
  1. Load recent 24h 1m candles into an in-memory dict-based environment.
  2. Instantiate the strategy and execute it against the candle data.
  3. If any exception occurs, capture the traceback for LLM retry.
  4. Return a pass/fail result with diagnostics.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PreFlightResult:
    """Result of a pre-flight dry-run."""

    success: bool
    bars_processed: int = 0
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None

    @property
    def retry_payload(self) -> dict[str, Any] | None:
        """Return a structured payload suitable for LLM retry requests."""
        if self.success:
            return None
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "traceback": self.traceback,
            "bars_processed_before_error": self.bars_processed,
            "instruction": (
                "The strategy code raised a runtime error during a sandbox "
                "dry-run against real 1m candle data. Please fix the code "
                "and return the corrected version."
            ),
        }


# ---------------------------------------------------------------------------
# Minimal in-memory context that mimics the real StrategyContext / LiveContext
# ---------------------------------------------------------------------------


class _SandboxPosition:
    def __init__(self) -> None:
        self.size: float = 0.0
        self.entry_price: float = 0.0
        self.unrealized_pnl: float = 0.0


class _SandboxContext:
    """Lightweight mock of StrategyContext for dry-run execution.

    Only exposes the subset of API that strategies rely on during on_bar().
    All order methods are no-ops that simply record calls.
    """

    def __init__(self, initial_balance: float = 10_000.0) -> None:
        self.balance: float = initial_balance
        self.initial_balance: float = initial_balance
        self.current_price: float = 0.0
        self.position_size: float = 0.0
        self.position: _SandboxPosition = _SandboxPosition()
        self.entry_price: float = 0.0
        self.candle_interval: str = "1m"
        self.leverage: int = 1

        # Internal indicator storage
        self._indicators: dict[str, list[float]] = {}
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._volumes: list[float] = []
        self._order_log: list[dict[str, Any]] = []

    # -- Price helpers -------------------------------------------------------

    def update_bar(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> None:
        self.current_price = close
        self._closes.append(close)
        self._highs.append(high)
        self._lows.append(low)
        self._volumes.append(volume)

    def mark_price(self, price: float) -> None:
        self.current_price = price

    # -- Indicator helpers ---------------------------------------------------

    def get_indicator(self, name: str, period: int = 14) -> float:
        return 50.0  # neutral default

    def add_indicator(self, name: str, value: float) -> None:
        self._indicators.setdefault(name, []).append(value)

    def get_indicator_values(self, config: dict[str, Any] | None = None) -> dict[str, float]:
        return {k: v[-1] for k, v in self._indicators.items() if v}

    def set_indicator_config(self, config: dict[str, Any]) -> None:
        pass

    def get_indicator_config(self) -> dict[str, Any]:
        return {}

    def set_strategy_meta(self, strategy: Any) -> None:
        pass

    # -- Order stubs (no-ops) ------------------------------------------------

    def buy(self, size: float = 1.0, **kwargs: Any) -> None:
        self._order_log.append({"action": "buy", "size": size, **kwargs})
        self.position_size += size
        self.position.size = self.position_size
        self.entry_price = self.current_price
        self.position.entry_price = self.current_price

    def sell(self, size: float = 1.0, **kwargs: Any) -> None:
        self._order_log.append({"action": "sell", "size": size, **kwargs})
        self.position_size -= size
        self.position.size = self.position_size

    def close_position(self, **kwargs: Any) -> None:
        self._order_log.append({"action": "close", **kwargs})
        self.position_size = 0.0
        self.position.size = 0.0

    def check_stoploss(self) -> bool:
        return False

    # Aliases used by various generated strategies
    market_buy = buy
    market_sell = sell
    limit_buy = buy
    limit_sell = sell


# ---------------------------------------------------------------------------
# Strategy loader (isolated module load)
# ---------------------------------------------------------------------------


def _load_strategy_from_code(code: str) -> Any:
    """Dynamically load a strategy class from source code string."""
    module_name = f"_preflight_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    if spec is None:
        raise RuntimeError("Failed to create module spec for pre-flight check")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        exec(compile(code, f"<preflight:{module_name}>", "exec"), module.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(module_name, None)

    # Find the Strategy subclass
    from strategy.base import Strategy

    for name in dir(module):
        obj = getattr(module, name)
        if (
            isinstance(obj, type)
            and name.endswith("Strategy")
            and obj is not Strategy
            and issubclass(obj, Strategy)
        ):
            return obj()

    raise ValueError("No Strategy subclass found in generated code")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_pre_flight(
    strategy_code: str,
    candle_data: list[dict[str, Any]],
    initial_balance: float = 10_000.0,
) -> PreFlightResult:
    """Execute a strategy dry-run in a sandboxed in-memory environment.

    Args:
        strategy_code: Python source code of the strategy.
        candle_data: List of 1m candle dicts with keys:
            {timestamp, open, high, low, close, volume}.
        initial_balance: Virtual starting balance for the sandbox.

    Returns:
        PreFlightResult indicating pass/fail with optional traceback.
    """
    if not candle_data:
        return PreFlightResult(
            success=False,
            error_type="NoData",
            error_message="No candle data provided for pre-flight check",
        )

    ctx = _SandboxContext(initial_balance=initial_balance)
    bars_processed = 0

    try:
        strategy = _load_strategy_from_code(strategy_code)
        strategy.initialize(ctx)

        for bar in candle_data:
            open_ = float(bar.get("open", 0))
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))
            close = float(bar.get("close", 0))
            volume = float(bar.get("volume", 0))

            ctx.update_bar(open_, high, low, close, volume)

            strategy.on_bar(ctx, bar)
            bars_processed += 1

    except Exception as exc:
        tb = traceback.format_exc()
        logger.warning(
            "Pre-flight check FAILED after %d bars: %s: %s",
            bars_processed,
            type(exc).__name__,
            exc,
        )
        return PreFlightResult(
            success=False,
            bars_processed=bars_processed,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback=tb,
        )

    logger.info(
        "Pre-flight check PASSED: %d bars processed, %d orders simulated",
        bars_processed,
        len(ctx._order_log),
    )
    return PreFlightResult(success=True, bars_processed=bars_processed)


async def run_pre_flight_with_retry(
    strategy_code: str,
    candle_data: list[dict[str, Any]],
    repair_callback: Any | None = None,
    max_retries: int = 2,
    initial_balance: float = 10_000.0,
) -> tuple[str, PreFlightResult]:
    """Run pre-flight check with automatic LLM retry on failure.

    Args:
        strategy_code: Initial strategy code to test.
        candle_data: Candle data for dry-run.
        repair_callback: Async callable(code, error_payload) -> repaired_code.
            If None, retries are skipped.
        max_retries: Maximum number of LLM repair attempts.
        initial_balance: Virtual starting balance.

    Returns:
        Tuple of (final_code, PreFlightResult).
    """
    current_code = strategy_code

    for attempt in range(1 + max_retries):
        result = run_pre_flight(current_code, candle_data, initial_balance)

        if result.success:
            if attempt > 0:
                logger.info("Pre-flight passed after %d repair attempt(s)", attempt)
            return current_code, result

        if attempt >= max_retries or repair_callback is None:
            logger.error(
                "Pre-flight FAILED after %d attempt(s), no more retries",
                attempt + 1,
            )
            return current_code, result

        # Request LLM repair
        logger.info(
            "Pre-flight attempt %d failed, requesting LLM repair", attempt + 1
        )
        try:
            repaired = await repair_callback(current_code, result.retry_payload)
            if repaired and repaired.strip():
                current_code = repaired
            else:
                logger.warning("LLM repair returned empty code, aborting retries")
                return current_code, result
        except Exception as repair_exc:
            logger.error("LLM repair request failed: %s", repair_exc)
            return current_code, result

    return current_code, result  # type: ignore[possibly-undefined]
