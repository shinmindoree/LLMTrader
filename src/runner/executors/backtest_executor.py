from __future__ import annotations

import asyncio
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.context import BacktestContext
from backtest.data_fetcher import fetch_all_klines
from backtest.engine import BacktestEngine
from backtest.risk import BacktestRiskManager
from binance.client import BinanceHTTPClient
from common.risk import RiskConfig
from control.enums import EventKind
from runner.event_sink import DbEventSink
from runner.strategy_loader import build_strategy, load_strategy_class, resolve_strategy_file
from settings import get_settings


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _as_float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:  # noqa: BLE001
        return None
    if not math.isfinite(number):
        return None
    return number


def _infer_indicator_pane(indicator_name: str, output_name: str | None) -> str:
    overlay_indicators = {"EMA", "SMA", "WMA", "MA", "MAX", "MIN"}
    if indicator_name.upper() in overlay_indicators:
        return "overlay"
    output = (output_name or "").lower()
    if output in {"ma", "upper", "lower"}:
        return "overlay"
    return "oscillator"


def _build_indicator_calls(indicator_name: str, raw_params: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_params, dict):
        return [{"label": indicator_name, "kwargs": {}}]

    name_upper = indicator_name.upper()
    if name_upper in {"EMA", "SMA", "WMA"} and "period" not in raw_params and "timeperiod" not in raw_params:
        calls: list[dict[str, Any]] = []
        for alias, value in raw_params.items():
            if not isinstance(value, (int, float)):
                continue
            period_value: int | float
            if isinstance(value, int) or float(value).is_integer():
                period_value = int(value)
            else:
                period_value = float(value)
            calls.append(
                {
                    "label": f"{indicator_name}({alias}={value})",
                    "kwargs": {"period": period_value},
                }
            )
        if calls:
            return calls

    return [{"label": indicator_name, "kwargs": dict(raw_params)}]


def _collect_backtest_chart_data(
    *,
    strategy: Any,
    symbol: str,
    interval: str,
    leverage: int,
    commission: float,
    klines: list[list[Any]],
    risk_manager: BacktestRiskManager,
) -> dict[str, Any]:
    candles: list[dict[str, Any]] = []
    for kline in klines:
        candles.append(
            {
                "open_time": int(kline[0]),
                "close_time": int(kline[6]),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            }
        )

    raw_indicator_config = getattr(strategy, "indicator_config", {})
    indicator_config = _json_safe(raw_indicator_config) if isinstance(raw_indicator_config, dict) else {}
    if not isinstance(indicator_config, dict):
        indicator_config = {}

    chart_data: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "candles": candles,
        "indicator_config": indicator_config,
        "indicator_series": [],
    }

    if not indicator_config:
        return chart_data

    analysis_ctx = BacktestContext(
        symbol=symbol,
        leverage=leverage,
        initial_balance=1000.0,
        risk_manager=risk_manager,
        commission_rate=commission,
    )

    try:
        strategy.initialize(analysis_ctx)
    except Exception:  # noqa: BLE001
        # 차트 보조 데이터 생성 실패가 백테스트 성공 자체를 깨지 않도록 방어.
        return chart_data

    indicator_calls: list[dict[str, Any]] = []
    for indicator_name, params in indicator_config.items():
        calls = _build_indicator_calls(str(indicator_name), params)
        for call in calls:
            indicator_calls.append(
                {
                    "indicator": str(indicator_name),
                    "label": str(call.get("label") or indicator_name),
                    "kwargs": call.get("kwargs") if isinstance(call.get("kwargs"), dict) else {},
                }
            )

    series_map: dict[str, dict[str, Any]] = {}

    def _set_series_value(
        *,
        index: int,
        call_label: str,
        indicator_name: str,
        output_name: str | None,
        value: float | None,
    ) -> None:
        series_key = f"{call_label}:{output_name or 'value'}"
        item = series_map.get(series_key)
        if item is None:
            pane = _infer_indicator_pane(indicator_name=indicator_name, output_name=output_name)
            label = call_label if output_name is None else f"{call_label}.{output_name}"
            item = {
                "id": series_key,
                "indicator": indicator_name,
                "output": output_name,
                "label": label,
                "pane": pane,
                "values": [None] * (index + 1),
            }
            series_map[series_key] = item
        values = item["values"]
        if len(values) < index + 1:
            values.extend([None] * (index + 1 - len(values)))
        values[index] = value

    for i, candle in enumerate(candles):
        analysis_ctx.update_bar(
            open_price=float(candle["open"]),
            high_price=float(candle["high"]),
            low_price=float(candle["low"]),
            close_price=float(candle["close"]),
            volume=float(candle["volume"]),
        )
        analysis_ctx.update_price(float(candle["close"]), timestamp=int(candle["close_time"]))

        for call in indicator_calls:
            indicator_name = str(call["indicator"])
            call_label = str(call["label"])
            kwargs = call["kwargs"] if isinstance(call["kwargs"], dict) else {}

            try:
                raw_value = analysis_ctx.get_indicator(indicator_name, **kwargs)
            except Exception:  # noqa: BLE001
                continue

            if isinstance(raw_value, dict):
                for output_name, sub_value in raw_value.items():
                    _set_series_value(
                        index=i,
                        call_label=call_label,
                        indicator_name=indicator_name,
                        output_name=str(output_name),
                        value=_as_float_or_none(sub_value),
                    )
                continue

            _set_series_value(
                index=i,
                call_label=call_label,
                indicator_name=indicator_name,
                output_name=None,
                value=_as_float_or_none(raw_value),
            )

    chart_data["indicator_series"] = [
        {
            "id": item["id"],
            "indicator": item["indicator"],
            "output": item["output"],
            "label": item["label"],
            "pane": item["pane"],
            "values": item["values"][: len(candles)],
        }
        for item in series_map.values()
    ]
    return chart_data


async def run_backtest(
    *,
    repo_root: Path,
    strategy_path: str,
    config: dict[str, Any],
    sink: DbEventSink,
    should_stop: asyncio.Event,
) -> dict[str, Any]:
    symbol = str(config.get("symbol") or "BTCUSDT").upper()
    interval = str(config.get("interval") or "1h")
    leverage = int(config.get("leverage") or 1)
    initial_balance = float(config.get("initial_balance") or 1000.0)
    commission = float(config.get("commission") or 0.0004)
    stop_loss_pct = float(config.get("stop_loss_pct") or 0.05)
    start_ts = int(config.get("start_ts") or 0)
    end_ts = int(config.get("end_ts") or 0)
    strategy_params = config.get("strategy_params") or {}
    strategy_code_snapshot = config.get("_strategy_code")

    sink.emit(kind=EventKind.LOG, message="BACKTEST_START", payload={"symbol": symbol, "interval": interval})

    settings = get_settings()
    backtest_url = settings.binance.base_url_backtest or "https://fapi.binance.com"
    client = BinanceHTTPClient(
        api_key="",
        api_secret="",
        base_url=backtest_url,
        timeout=60.0,
    )

    try:
        klines = await fetch_all_klines(
            client=client,
            symbol=symbol,
            interval=interval,
            start_ts=start_ts,
            end_ts=end_ts,
            progress_callback=lambda p: sink.emit(kind=EventKind.PROGRESS, message="DATA_FETCH", payload={"pct": p}),
        )
        if should_stop.is_set():
            return {"stopped": True}

        if not klines:
            raise ValueError("No klines returned for backtest")

        risk_config = RiskConfig(
            max_leverage=float(leverage),
            max_position_size=float(config.get("max_position") or 0.5),
            max_order_size=float(config.get("max_position") or 0.5),
            stop_loss_pct=stop_loss_pct,
            max_pyramid_entries=int(config.get("max_pyramid_entries") or 0),
        )
        risk_manager = BacktestRiskManager(risk_config)
        fixed_notional_raw = config.get("fixed_notional")
        try:
            fixed_notional = float(fixed_notional_raw) if fixed_notional_raw not in (None, "") else None
        except (TypeError, ValueError):
            fixed_notional = None
        ctx = BacktestContext(
            symbol=symbol,
            leverage=leverage,
            initial_balance=initial_balance,
            risk_manager=risk_manager,
            commission_rate=commission,
            fixed_notional=fixed_notional,
        )

        strategy_file, cleanup_strategy_file = resolve_strategy_file(
            repo_root=repo_root,
            strategy_path=strategy_path,
            fallback_code=str(strategy_code_snapshot) if isinstance(strategy_code_snapshot, str) else None,
        )
        strategy_class = load_strategy_class(strategy_file)
        strategy = build_strategy(strategy_class, dict(strategy_params) if isinstance(strategy_params, dict) else {})

        _last_reported_pct = 0.0

        def progress_cb(pct: float) -> None:
            nonlocal _last_reported_pct
            if pct - _last_reported_pct >= 0.5 or pct >= 100.0:
                sink.emit_from_thread(kind=EventKind.PROGRESS, message="BACKTEST_PROGRESS", payload={"pct": pct})
                _last_reported_pct = pct
            if should_stop.is_set():
                raise RuntimeError("STOP_REQUESTED")

        engine = BacktestEngine(strategy=strategy, context=ctx, klines=klines, progress_callback=progress_cb)
        results = await asyncio.to_thread(engine.run)

        sink.emit(kind=EventKind.PROGRESS, message="BACKTEST_PROGRESS", payload={"pct": 100.0})

        # Attach trades for UI (summary only; large lists can be paged later)
        results["num_trades"] = len(ctx.trades)
        results["finished_at"] = datetime.now().isoformat()
        results["chart"] = _collect_backtest_chart_data(
            strategy=strategy,
            symbol=symbol,
            interval=interval,
            leverage=leverage,
            commission=commission,
            klines=klines,
            risk_manager=risk_manager,
        )
        return results
    finally:
        if "cleanup_strategy_file" in locals() and cleanup_strategy_file:
            strategy_file.unlink(missing_ok=True)
        await client.aclose()
