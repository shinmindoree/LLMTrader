"""Quick (synchronous) backtest for strategy builder iteration loop.

Safety controls:
- Global semaphore: max 3 concurrent backtests across all instances
- Per-user lock: max 1 concurrent backtest per user
- Daily quota: Free 10, Pro 100, Enterprise unlimited
- Interval-based day limits: 1m≤7d, 5m≤30d, 15m≤60d, 1h+≤90d
- Timeout: 90 seconds per execution
"""

from __future__ import annotations

import asyncio
import logging
import math
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from api.kline_cache import (
    get_cached_klines,
    get_daily_quota_count,
    increment_daily_quota,
    set_cached_klines,
)
from api.plans import get_plan_limits
from api.schemas import (
    QuickBacktestEquityPoint,
    QuickBacktestMetrics,
    QuickBacktestRequest,
    QuickBacktestResponse,
    QuickBacktestTrade,
)
from backtest.context import BacktestContext
from backtest.engine import BacktestEngine
from backtest.risk import BacktestRiskManager
from binance.client import BinanceHTTPClient, normalize_binance_base_url
from common.risk import RiskConfig
from runner.strategy_loader import build_strategy, load_strategy_class
from settings import get_settings

logger = logging.getLogger(__name__)

# ── Safety controls ─────────────────────────────────────────

_global_semaphore = asyncio.Semaphore(3)
_user_locks: dict[str, asyncio.Lock] = {}
_user_locks_guard = asyncio.Lock()

INTERVAL_MAX_DAYS: dict[str, int] = {
    "1m": 7,
    "3m": 14,
    "5m": 30,
    "15m": 60,
    "30m": 90,
    "1h": 90,
    "2h": 90,
    "4h": 90,
    "6h": 90,
    "8h": 90,
    "12h": 90,
    "1d": 90,
    "3d": 90,
    "1w": 90,
    "1M": 90,
}

QUICK_BACKTEST_TIMEOUT = 90


async def _get_user_lock(user_id: str) -> asyncio.Lock:
    async with _user_locks_guard:
        if user_id not in _user_locks:
            _user_locks[user_id] = asyncio.Lock()
        return _user_locks[user_id]


def _error_response(
    error_code: str,
    message: str,
    quota_remaining: int | None = None,
) -> QuickBacktestResponse:
    return QuickBacktestResponse(
        success=False,
        error_code=error_code,
        message=message,
        quota_remaining=quota_remaining,
    )


def _validate_request(req: QuickBacktestRequest) -> str | None:
    """Validate request parameters. Returns error message or None."""
    if req.interval not in INTERVAL_MAX_DAYS:
        return f"지원하지 않는 인터벌입니다: {req.interval}"

    max_days = INTERVAL_MAX_DAYS[req.interval]
    if req.days > max_days:
        return f"{req.interval}은 최대 {max_days}일까지 테스트할 수 있습니다. 기간을 줄이거나 더 긴 인터벌을 선택해주세요."

    symbol = req.symbol.strip().upper()
    if not symbol or len(symbol) < 2 or len(symbol) > 20:
        return "올바른 심볼을 입력해주세요."

    return None


async def _check_quota(user_id: str, plan: str) -> tuple[bool, int | None, str]:
    """Check daily quota. Returns (ok, remaining, message)."""
    limits = get_plan_limits(plan)
    max_daily = limits.max_quick_backtest_per_day

    if max_daily >= 9999:
        return True, None, ""

    count = await get_daily_quota_count(user_id)
    if count is None:
        # Redis unavailable — allow but don't track
        return True, None, ""

    remaining = max(0, max_daily - count)
    if count >= max_daily:
        if plan == "free":
            msg = (
                f"오늘의 무료 테스트 횟수({max_daily}회)를 모두 사용했습니다. "
                "Pro 플랜으로 업그레이드하면 일 100회까지 테스트할 수 있습니다."
            )
        else:
            msg = f"오늘의 테스트 횟수({max_daily}회)를 모두 사용했습니다. 내일 초기화됩니다."
        return False, 0, msg

    return True, remaining, ""


async def _fetch_klines_cached(
    symbol: str,
    interval: str,
    start_ts: int,
    end_ts: int,
) -> list[list[Any]]:
    """Fetch klines with Redis cache layer."""
    cached = await get_cached_klines(symbol, interval, start_ts, end_ts)
    if cached is not None:
        logger.info("Kline cache hit: %s %s", symbol, interval)
        return cached

    settings = get_settings()
    base_url = settings.binance.base_url_backtest or settings.binance.base_url
    client = BinanceHTTPClient(
        api_key="",
        api_secret="",
        base_url=base_url,
    )

    try:
        from backtest.data_fetcher import fetch_all_klines

        klines = await fetch_all_klines(
            client,
            symbol=symbol,
            interval=interval,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    finally:
        await client.aclose()

    if klines:
        await set_cached_klines(symbol, interval, start_ts, end_ts, klines)

    return klines


def _compute_metrics(
    engine_result: dict[str, Any],
    initial_balance: float,
) -> QuickBacktestMetrics:
    """Extract structured metrics from backtest engine result."""
    trades = engine_result.get("trades", [])
    final_balance = float(engine_result.get("final_balance", initial_balance))
    total_pnl = float(engine_result.get("total_pnl", 0.0))
    total_return_pct = ((final_balance - initial_balance) / initial_balance * 100) if initial_balance else 0.0
    total_commission = float(engine_result.get("total_commission", 0.0))

    # Win rate
    sell_trades = [t for t in trades if t.get("side") == "SELL"]
    winning = [t for t in sell_trades if float(t.get("pnl", 0)) > 0]
    total_trades = len(sell_trades)
    win_rate = (len(winning) / total_trades * 100) if total_trades > 0 else 0.0

    # Avg win / avg loss
    wins = [float(t.get("pnl", 0)) for t in sell_trades if float(t.get("pnl", 0)) > 0]
    losses = [float(t.get("pnl", 0)) for t in sell_trades if float(t.get("pnl", 0)) < 0]
    avg_win_pct = (sum(wins) / len(wins) / initial_balance * 100) if wins else 0.0
    avg_loss_pct = (sum(losses) / len(losses) / initial_balance * 100) if losses else 0.0

    # Max drawdown
    equity_peak = initial_balance
    max_dd = 0.0
    running_balance = initial_balance
    for t in trades:
        pnl = float(t.get("pnl", 0))
        commission = float(t.get("commission", 0))
        running_balance += pnl - commission
        if running_balance > equity_peak:
            equity_peak = running_balance
        dd = (equity_peak - running_balance) / equity_peak if equity_peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (simplified: daily returns approximation)
    sharpe = 0.0
    if total_trades >= 2:
        returns = [(float(t.get("pnl", 0)) - float(t.get("commission", 0))) / initial_balance for t in sell_trades]
        if returns:
            import statistics

            mean_r = statistics.mean(returns)
            std_r = statistics.stdev(returns) if len(returns) > 1 else 0.0
            sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0.0
            if not math.isfinite(sharpe):
                sharpe = 0.0

    return QuickBacktestMetrics(
        initial_balance=initial_balance,
        final_balance=round(final_balance, 2),
        total_return_pct=round(total_return_pct, 2),
        total_pnl=round(total_pnl, 2),
        total_trades=total_trades,
        win_rate=round(win_rate, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        sharpe_ratio=round(sharpe, 2),
        avg_win_pct=round(avg_win_pct, 2),
        avg_loss_pct=round(avg_loss_pct, 2),
        net_profit=round(final_balance - initial_balance, 2),
        total_commission=round(total_commission, 4),
    )


def _build_trades_summary(engine_result: dict[str, Any]) -> list[QuickBacktestTrade]:
    """Build lightweight trade list from engine result."""
    trades = engine_result.get("trades", [])
    summary: list[QuickBacktestTrade] = []
    entry_price = 0.0

    for t in trades:
        side = t.get("side", "")
        price = float(t.get("price", 0))
        qty = float(t.get("quantity", 0))
        pnl = float(t.get("pnl", 0))

        if side == "BUY":
            entry_price = price
        elif side == "SELL":
            ret_pct = ((price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
            summary.append(QuickBacktestTrade(
                side="LONG" if entry_price < price or pnl > 0 else "SHORT",
                entry_price=round(entry_price, 2),
                exit_price=round(price, 2),
                quantity=round(qty, 6),
                pnl=round(pnl, 2),
                return_pct=round(ret_pct, 2),
            ))

    return summary


def _build_equity_curve(engine_result: dict[str, Any], initial_balance: float) -> list[QuickBacktestEquityPoint]:
    """Build lightweight equity curve from trade sequence."""
    trades = engine_result.get("trades", [])
    curve: list[QuickBacktestEquityPoint] = []
    balance = initial_balance

    for t in trades:
        pnl = float(t.get("pnl", 0))
        commission = float(t.get("commission", 0))
        ts = int(t.get("timestamp", 0))
        balance += pnl - commission
        if t.get("side") == "SELL" and ts > 0:
            curve.append(QuickBacktestEquityPoint(ts=ts, balance=round(balance, 2)))

    return curve


async def run_quick_backtest(
    req: QuickBacktestRequest,
    user_id: str,
    plan: str,
) -> QuickBacktestResponse:
    """Execute a quick backtest with all safety guards."""
    start_time = time.monotonic()

    # 1. Validate request
    validation_error = _validate_request(req)
    if validation_error:
        return _error_response("INVALID_REQUEST", validation_error)

    # 2. Check daily quota
    quota_ok, remaining, quota_msg = await _check_quota(user_id, plan)
    if not quota_ok:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return _error_response(
            "QUOTA_EXCEEDED",
            quota_msg,
            quota_remaining=0,
        )

    # 3. Per-user concurrency check
    user_lock = await _get_user_lock(user_id)
    if user_lock.locked():
        return _error_response(
            "USER_BUSY",
            "이미 실행 중인 테스트가 있습니다. 완료 후 다시 시도해주세요.",
            quota_remaining=remaining,
        )

    # 4. Global semaphore check
    if _global_semaphore.locked() and _global_semaphore._value == 0:
        return _error_response(
            "SERVER_BUSY",
            "현재 많은 분들이 테스트 중입니다. 잠시 후 다시 시도해주세요.",
            quota_remaining=remaining,
        )

    async with user_lock:
        try:
            async with _global_semaphore:
                result = await asyncio.wait_for(
                    _execute_backtest(req),
                    timeout=QUICK_BACKTEST_TIMEOUT,
                )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return _error_response(
                "TIMEOUT",
                "테스트 실행 시간이 초과되었습니다. 기간을 줄이거나 더 긴 인터벌로 다시 시도해주세요.",
                quota_remaining=remaining,
            )

    # 5. Increment quota on success
    new_count = await increment_daily_quota(user_id)
    limits = get_plan_limits(plan)
    if new_count is not None and limits.max_quick_backtest_per_day < 9999:
        remaining = max(0, limits.max_quick_backtest_per_day - new_count)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    if not result["success"]:
        return _error_response(
            result.get("error_code", "BACKTEST_ERROR"),
            result.get("message", "백테스트 실행 중 오류가 발생했습니다."),
            quota_remaining=remaining,
        )

    engine_result = result["engine_result"]
    metrics = _compute_metrics(engine_result, req.initial_balance)
    trades_summary = _build_trades_summary(engine_result)
    equity_curve = _build_equity_curve(engine_result, req.initial_balance)

    return QuickBacktestResponse(
        success=True,
        metrics=metrics,
        trades_summary=trades_summary,
        equity_curve=equity_curve,
        duration_ms=duration_ms,
        quota_remaining=remaining,
    )


async def _execute_backtest(req: QuickBacktestRequest) -> dict[str, Any]:
    """Core backtest execution in a thread."""
    import ast as _ast

    code = req.code.strip()
    if not code:
        return {"success": False, "error_code": "INVALID_CODE", "message": "전략 코드가 비어있습니다."}

    # AST validation
    try:
        _ast.parse(code)
    except SyntaxError as exc:
        return {
            "success": False,
            "error_code": "SYNTAX_ERROR",
            "message": f"전략 코드에 문제가 있습니다: 라인 {exc.lineno} - {exc.msg}. 코드를 수정한 후 다시 시도해주세요.",
        }

    # Post-process: inject OHLCV bindings if needed (safety net for LLM-generated code)
    try:
        from llm.strategy_postprocess import ensure_ohlcv_bindings

        code = ensure_ohlcv_bindings(code)
    except Exception:
        pass  # non-critical; proceed with original code

    symbol = req.symbol.strip().upper()
    now = datetime.now(timezone.utc)
    end_dt = now - timedelta(minutes=1)
    start_dt = end_dt - timedelta(days=req.days)
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    # Fetch klines (cached)
    try:
        klines = await _fetch_klines_cached(symbol, req.interval, start_ts, end_ts)
    except Exception as exc:
        logger.warning("Kline fetch failed: %s", exc)
        return {
            "success": False,
            "error_code": "DATA_ERROR",
            "message": f"시장 데이터 조회에 실패했습니다: {exc}",
        }

    if not klines:
        return {
            "success": False,
            "error_code": "NO_DATA",
            "message": f"{symbol} {req.interval}에 대한 데이터가 없습니다. 심볼과 인터벌을 확인해주세요.",
        }

    # Write strategy to temp file and execute
    tmp_dir = Path(tempfile.mkdtemp(prefix="quick_bt_"))
    tmp_file = tmp_dir / f"strategy_{uuid.uuid4().hex[:8]}.py"
    try:
        tmp_file.write_text(code, encoding="utf-8")
        strategy_class = load_strategy_class(tmp_file)
        params = dict(req.strategy_params) if req.strategy_params else {}
        strategy = build_strategy(strategy_class, params)

        risk_config = RiskConfig(
            max_leverage=float(req.leverage),
            max_position_size=1.0,
            stop_loss_pct=req.stop_loss_pct,
        )
        risk_manager = BacktestRiskManager(risk_config)
        ctx = BacktestContext(
            symbol=symbol,
            leverage=req.leverage,
            initial_balance=req.initial_balance,
            risk_manager=risk_manager,
            commission_rate=req.commission,
        )
        engine = BacktestEngine(strategy=strategy, context=ctx, klines=klines)

        # Run in thread to avoid blocking the event loop
        engine_result = await asyncio.to_thread(engine.run)

        return {"success": True, "engine_result": engine_result}

    except Exception as exc:
        logger.warning("Quick backtest execution error: %s", exc, exc_info=True)
        return {
            "success": False,
            "error_code": "EXECUTION_ERROR",
            "message": f"전략 코드에 문제가 있습니다: {exc}. 코드를 수정한 후 다시 시도해주세요.",
        }
    finally:
        try:
            tmp_file.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass
