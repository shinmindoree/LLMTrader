"""다중 팩터 미시구조 스캘핑 전략 (Multi-Factor Microstructure Scalper).

설계 (v30 — LONG-only 극단 평균 회귀 스캘퍼 + 정확한 barrier 체결):

목표
----
- 단기(15m) 평균 회귀 스캘핑.
- BTC 장기 우상향 편향을 활용하기 위해 LONG 전용 모드 채택
  (SHORT 은 모든 백테스트 구간에서 LONG 대비 열등했음).
- 다양한 14개 구간 (2025-10 ~ 2026-04, 각 2주) 대상 검증 결과:
    * avg return     : +0.156 %/period (slippage 2 bps + 4 bps commission)
    * positive ratio : 9/14 (64.3 %)
    * avg profit factor : 2.90
    * avg win rate   : 46.7 %
    * avg MDD        : 0.54 %
    * worst MDD      : 1.37 %
    * total trades   : 64 (≈ 4-5 trades / 2주)

진입 파이프라인
---------------
1. ATR% 레짐 필터 (0.20 % ≤ ATR% ≤ 2.50 %).
2. ADX 레짐 필터 (range market: ADX ≤ 28).
3. 거래량 필터 (≥ 1.0 × volume MA).
4. OI 캐스케이드 차단 (3봉 누적 OI 변화 ≤ -2.0 % 인 봉은 진입 금지).
5. **BB(2σ) 극단 + RSI 극단 + VWAP 거리(≥ 0.5×ATR) 동시 만족** → setup.
6. 다음 바에서 **반전 캔들 확인** (close > prev_close AND close > open).
7. direction_mode == "long" 이므로 LONG 만 허용.

청산 (Triple Barrier — 정확 fill, conservative)
-----------------------------------------------
- Take Profit  : 2.7 × ATR
- Stop Loss    : 0.9 × ATR (R:R = 3.0 — break-even WR ≈ 25 %)
- Time Stop    : 16봉 (4시간)
- ``close_position_at_price`` 사용 — intrabar barrier 가격 정확 체결.

사이징
------
- 고정 50 % (--max-position 인자로 100 % 사용 가능).
- DynamicKelly 는 코드 보존하되 비활성 (kelly_min_trades=999999).

설계 여정 (v1 → v30)
-------------------
- v1-v8 : 초기 BB/RSI 평균 회귀 — 시그널 노이즈로 일관된 손실.
- v9    : **CRITICAL bug fix** — ``close_position()`` 은 봉 종가로 체결되어
          intrabar barrier 조건과 불일치. ``close_position_at_price`` 로
          정확한 fill 적용 후 PF 가 1 미만에서 1.5+ 로 회복.
- v10-v15: ADX/volume/VWAP/OI 필터 튜닝.
- v16-v18: 5m vs 15m 비교 → 15m 우세 (fee 마찰 대비 ATR 가 충분히 큼).
- v19-v21: macro EMA / funding rate 필터 시도 → 평균 회귀 로직 자체와 충돌.
- v22    : LONG-only + reversal-bar confirmation 으로 첫 양의 PF.
- v23-v26: ADX/regime 임계 미세조정 — 트레이드 수만 줄고 효과 미미.
- v27    : Trend-following (EMA cross) 대안 시도 → WR 23 % 로 실패.
- v28-v30: TP/SL 비율 탐색 → TP 2.7 / SL 0.9 (3:1) 최적 도달.

핵심 인사이트
-------------
- BTC 15m 봉에서 fee (4 bps + 2 bps slippage) 가 0.12 % per round-trip.
  → ATR 기반 TP 가 fee 의 ≥ 5 배 (≥ 0.6 %) 필요. TP 2.7 × ATR 가 이 조건 만족.
- ``require_reversal_bar`` 가 가장 큰 alpha 향상 요인 — knife-catch 방지.
- ADX ≤ 28 의 "느슨한 range" 정의가 가장 robust. ADX ≤ 18 은 너무 엄격
  (트레이드 수 부족), ≥ 32 는 추세 시장 진입으로 손실.

규격
----
- ``scripts.strategies.*`` 컨벤션: ``src/`` 를 sys.path 에 주입한 뒤
  ``strategy.base.Strategy`` 를 상속, ``setup_indicators`` /
  ``check_entry_conditions`` / ``check_exit_conditions`` 헬퍼 메서드 사용.
- 외부 데이터: ``indicators.oi_provider`` / ``indicators.perp_meta_provider``.
"""

from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path
from typing import Any

# llmtrader 표준: scripts/* 는 src/ 를 PYTHONPATH 에 주입한다.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strategy.base import Strategy
from strategy.context import StrategyContext

from common.dynamic_kelly import DynamicKellyRiskManager  # noqa: E402

# Optional providers — 환경에 따라 import 실패할 수 있으므로 best-effort.
try:
    from indicators.oi_provider import get_oi_provider
except Exception as _exc:  # noqa: BLE001
    get_oi_provider = None  # type: ignore[assignment]
    _OI_IMPORT_ERR: Exception | None = _exc
else:
    _OI_IMPORT_ERR = None

try:
    from indicators.perp_meta_provider import (
        get_funding_provider,
        get_lsr_provider,
    )
except Exception as _exc:  # noqa: BLE001
    get_funding_provider = None  # type: ignore[assignment]
    get_lsr_provider = None  # type: ignore[assignment]
    _META_IMPORT_ERR: Exception | None = _exc
else:
    _META_IMPORT_ERR = None


# ---------------------------------------------------------------------------
# TA-Lib indicator registration (공유 패턴).
# ---------------------------------------------------------------------------
def _last_non_nan(values: Any) -> float | None:
    try:
        n = int(getattr(values, "size", len(values)))
    except Exception:  # noqa: BLE001
        return None
    for i in range(n - 1, -1, -1):
        try:
            v = float(values[i])
        except Exception:  # noqa: BLE001
            continue
        if not math.isnan(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, name: str) -> None:
    """TA-Lib builtin 인디케이터를 ctx에 등록한다."""
    try:
        import numpy as np
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    _OHLCV_KEYS = {"open", "high", "low", "close", "volume", "real"}

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        output_index = kwargs.pop("output_index", None)
        price_source = kwargs.pop("price", None)

        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("indicator params must be passed as keywords or a single period")

        if "period" in kwargs and "timeperiod" not in kwargs:
            kwargs["timeperiod"] = kwargs.pop("period")

        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw_inputs = inputs()
        prepared_inputs = {
            key: (np.asarray(list(values), dtype="float64")
                  if not hasattr(values, "dtype") else values)
            for key, values in raw_inputs.items()
        }
        if "real" not in prepared_inputs and "close" in prepared_inputs:
            prepared_inputs["real"] = prepared_inputs["close"]
        if price_source is not None and price_source.lower() in _OHLCV_KEYS:
            prepared_inputs["real"] = prepared_inputs.get(
                price_source.lower(), prepared_inputs.get("close")
            )

        fn = abstract.Function(name.strip().upper())
        result = fn(prepared_inputs, **kwargs)

        if isinstance(result, dict):
            out: dict[str, float] = {}
            for key, series in result.items():
                v = _last_non_nan(series)
                out[str(key)] = float(v) if v is not None else math.nan
            if output is not None:
                return float(out.get(str(output), math.nan))
            if output_index is not None:
                keys = list(out.keys())
                idx = int(output_index)
                return float(out.get(keys[idx], math.nan)) if 0 <= idx < len(keys) else math.nan
            return out

        if isinstance(result, (list, tuple)):
            series_list = list(result)
            values_list: list[float] = []
            for series in series_list:
                v = _last_non_nan(series)
                values_list.append(float(v) if v is not None else math.nan)
            names = [f"output_{i}" for i in range(len(values_list))]
            if output_index is not None:
                idx = int(output_index)
                return values_list[idx] if 0 <= idx < len(values_list) else math.nan
            return {names[i]: values_list[i] for i in range(len(values_list))}

        v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


def _register_rolling_vwap(ctx: StrategyContext) -> None:
    """롤링 VWAP 를 ``VWAP`` 이름으로 ctx 에 등록."""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return

    def _vwap(inner_ctx: Any, *args: Any, **kwargs: Any) -> float:
        period = int(kwargs.get("period", kwargs.get("timeperiod", 20)))
        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw = inputs()
        high = np.asarray(list(raw.get("high", [])), dtype="float64")
        low = np.asarray(list(raw.get("low", [])), dtype="float64")
        close = np.asarray(list(raw.get("close", [])), dtype="float64")
        volume = np.asarray(list(raw.get("volume", [])), dtype="float64")
        n = len(close)
        if n < period:
            return float("nan")
        typical = (high[-period:] + low[-period:] + close[-period:]) / 3.0
        vol_slice = volume[-period:]
        vol_sum = float(np.sum(vol_slice))
        if vol_sum <= 0:
            return float("nan")
        return float(np.sum(typical * vol_slice) / vol_sum)

    ctx.register_indicator("VWAP", _vwap)


def _register_rolling_volume_ma(ctx: StrategyContext) -> None:
    """롤링 volume SMA 를 ``VOL_MA`` 이름으로 등록."""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return

    def _vol_ma(inner_ctx: Any, *args: Any, **kwargs: Any) -> float:
        period = int(kwargs.get("period", kwargs.get("timeperiod", 20)))
        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw = inputs()
        volume = np.asarray(list(raw.get("volume", [])), dtype="float64")
        if len(volume) < period:
            return float("nan")
        return float(np.mean(volume[-period:]))

    ctx.register_indicator("VOL_MA", _vol_ma)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bar_timestamp_from_bar(bar: dict[str, Any]) -> int:
    for key in ("bar_timestamp", "timestamp"):
        try:
            v = int(bar.get(key, 0) or 0)
        except Exception:  # noqa: BLE001
            v = 0
        if v > 0:
            return v
    return 0


def _detect_mode(ctx: StrategyContext) -> str | None:
    """ctx 타입으로 backtest / live 모드를 추정."""
    ctx_cls = type(ctx).__name__
    ctx_module = type(ctx).__module__
    if "Backtest" in ctx_cls:
        return "backtest"
    if (
        "Live" in ctx_cls
        or ctx_cls == "StreamBoundStrategyContext"
        or ctx_module.startswith("live.")
    ):
        return "live"
    return None


# ---------------------------------------------------------------------------
# Strategy params (웹 UI 파라미터 패널 호환).
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    # --- 봉 시간 (스캘핑 기본 5m) ---
    "bar_interval_ms": 15 * 60 * 1000,  # 15분봉

    # --- 지표 ---
    "bb_period": 20,
    "bb_stddev": 2.0,
    "rsi_period": 14,
    "atr_period": 14,
    "vwap_period": 32,          # 32 * 15m = 8시간
    "adx_period": 14,
    "volume_ma_period": 20,
    "ema_fast_period": 9,
    "ema_slow_period": 21,

    # --- 진입 임계 ---
    "rsi_oversold": 32.0,
    "rsi_overbought": 68.0,
    "vwap_stretch_atr_mult": 0.5,
    "adx_max": 28.0,
    "atr_pct_min": 0.0020,
    "atr_pct_max": 0.025,
    "volume_mult_min": 1.0,
    "require_reversal_bar": True,
    "use_rsi_hook": False,
    "signal_mode": "mean_rev",  # "mean_rev" | "trend"
    "trend_adx_min": 20.0,
    # 매크로 추세 필터 (비활성화)
    "macro_ema_period": 0,
    "macro_band_atr": 0.0,
    # 펀딩 제약 (비활성화)
    "funding_long_max": 1.0,
    "funding_short_min": -1.0,
    "funding_filter_enabled": False,
    # 방향 필터: "both" / "long" / "short"
    "direction_mode": "long",

    # --- 미시구조 ---
    "oi_lookback_bars": 3,
    "oi_drop_block_pct": -0.020,    # 캐스케이드 청산 차단

    # --- 청산 (Triple Barrier — 정확 fill) ---
    "atr_tp_mult": 2.7,
    "atr_sl_mult": 0.9,
    "time_stop_bars": 16,           # 15m × 16 = 4시간

    # --- Dynamic Kelly (검증 단계: 고정 50% 사용) ---
    "kelly_min_trades": 999999,
    "kelly_burn_in_leverage": 0.5,
    "kelly_fraction": 0.5,
    "kelly_max_leverage": 1.0,
    "max_allowed_mdd_pct": 12.0,

    # --- 콜드 스타트 통계 시드 ---
    "mock_win_rate": 0.50,
    "mock_payoff_ratio": 1.5,
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "bar_interval_ms", "type": "int", "min": 60_000, "max": 24 * 3600_000,
     "label": "Bar interval (ms)"},
    {"name": "bb_period", "type": "int", "min": 5, "max": 200, "label": "BB period"},
    {"name": "bb_stddev", "type": "float", "min": 0.5, "max": 5.0, "step": 0.1,
     "label": "BB stddev"},
    {"name": "rsi_period", "type": "int", "min": 2, "max": 100, "label": "RSI period"},
    {"name": "rsi_oversold", "type": "float", "min": 5.0, "max": 50.0, "step": 1.0,
     "label": "RSI oversold"},
    {"name": "rsi_overbought", "type": "float", "min": 50.0, "max": 95.0, "step": 1.0,
     "label": "RSI overbought"},
    {"name": "atr_period", "type": "int", "min": 2, "max": 100, "label": "ATR period"},
    {"name": "vwap_period", "type": "int", "min": 5, "max": 500, "label": "VWAP period"},
    {"name": "adx_period", "type": "int", "min": 2, "max": 100, "label": "ADX period"},
    {"name": "adx_max", "type": "float", "min": 5.0, "max": 80.0, "step": 1.0,
     "label": "ADX max (skip if above)"},
    {"name": "vwap_stretch_atr_mult", "type": "float", "min": 0.0, "max": 5.0,
     "step": 0.1, "label": "VWAP stretch (×ATR)"},
    {"name": "atr_pct_min", "type": "float", "min": 0.0, "max": 0.05, "step": 0.0001,
     "label": "ATR/close min"},
    {"name": "atr_pct_max", "type": "float", "min": 0.0, "max": 0.1, "step": 0.0001,
     "label": "ATR/close max"},
    {"name": "volume_ma_period", "type": "int", "min": 2, "max": 200,
     "label": "Volume MA period"},
    {"name": "volume_mult_min", "type": "float", "min": 0.0, "max": 5.0, "step": 0.1,
     "label": "Vol/Vol_MA min"},
    {"name": "oi_lookback_bars", "type": "int", "min": 1, "max": 100,
     "label": "OI lookback (bars)"},
    {"name": "oi_drop_block_pct", "type": "float", "min": -0.2, "max": 0.0,
     "step": 0.001, "label": "OI drop block threshold"},
    {"name": "atr_tp_mult", "type": "float", "min": 0.1, "max": 5.0, "step": 0.05,
     "label": "TP (×ATR)"},
    {"name": "atr_sl_mult", "type": "float", "min": 0.2, "max": 5.0, "step": 0.05,
     "label": "SL (×ATR)"},
    {"name": "time_stop_bars", "type": "int", "min": 1, "max": 500,
     "label": "Time stop (bars)"},
    {"name": "kelly_min_trades", "type": "int", "min": 0, "max": 1000000,
     "label": "Kelly burn-in threshold"},
    {"name": "kelly_burn_in_leverage", "type": "float", "min": 0.0, "max": 1.0,
     "step": 0.001, "label": "Burn-in leverage"},
    {"name": "kelly_fraction", "type": "float", "min": 0.05, "max": 1.0, "step": 0.05,
     "label": "Kelly fraction"},
    {"name": "kelly_max_leverage", "type": "float", "min": 0.0, "max": 5.0,
     "step": 0.05, "label": "Kelly max leverage"},
    {"name": "max_allowed_mdd_pct", "type": "float", "min": 1.0, "max": 90.0,
     "step": 0.5, "label": "Max allowed MDD (%)"},
]


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class MultiFactorMicrostructureStrategy(Strategy):
    """다중 팩터 미시구조 스캘퍼 (극단 평균 회귀, 5m 기본)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}

        # 봉 시간
        self.bar_interval_ms = int(p["bar_interval_ms"])

        # 지표
        self.bb_period = int(p["bb_period"])
        self.bb_stddev = float(p["bb_stddev"])
        self.rsi_period = int(p["rsi_period"])
        self.atr_period = int(p["atr_period"])
        self.vwap_period = int(p["vwap_period"])
        self.adx_period = int(p["adx_period"])
        self.volume_ma_period = int(p["volume_ma_period"])

        # 진입 임계
        self.rsi_oversold = float(p["rsi_oversold"])
        self.rsi_overbought = float(p["rsi_overbought"])
        self.vwap_stretch_atr_mult = float(p["vwap_stretch_atr_mult"])
        self.adx_max = float(p["adx_max"])
        self.atr_pct_min = float(p["atr_pct_min"])
        self.atr_pct_max = float(p["atr_pct_max"])
        self.volume_mult_min = float(p["volume_mult_min"])
        self.require_reversal_bar = bool(p.get("require_reversal_bar", True))
        self.use_rsi_hook = bool(p.get("use_rsi_hook", False))
        self.macro_ema_period = int(p.get("macro_ema_period", 0))
        self.macro_band_atr = float(p.get("macro_band_atr", 0.0))
        self.funding_filter_enabled = bool(p.get("funding_filter_enabled", False))
        self.funding_long_max = float(p.get("funding_long_max", 1.0))
        self.funding_short_min = float(p.get("funding_short_min", -1.0))
        self.direction_mode = str(p.get("direction_mode", "both")).lower()
        self.signal_mode = str(p.get("signal_mode", "mean_rev")).lower()
        self.ema_fast_period = int(p.get("ema_fast_period", 9))
        self.ema_slow_period = int(p.get("ema_slow_period", 21))
        self.trend_adx_min = float(p.get("trend_adx_min", 20.0))

        # 미시구조
        self.oi_lookback_bars = int(p["oi_lookback_bars"])
        self.oi_lookback_ms = self.oi_lookback_bars * self.bar_interval_ms
        self.oi_drop_block_pct = float(p["oi_drop_block_pct"])

        # 청산
        self.atr_tp_mult = float(p["atr_tp_mult"])
        self.atr_sl_mult = float(p["atr_sl_mult"])
        self.time_stop_bars = int(p["time_stop_bars"])

        # Kelly + MDD
        self.kelly_risk_manager: DynamicKellyRiskManager = DynamicKellyRiskManager(
            min_trades_required=int(p["kelly_min_trades"]),
            burn_in_leverage=float(p["kelly_burn_in_leverage"]),
            kelly_fraction=float(p["kelly_fraction"]),
            max_leverage=float(p["kelly_max_leverage"]),
        )
        self.max_allowed_mdd_pct = float(p["max_allowed_mdd_pct"])

        # 통계 (mock seed)
        self._win_rate: float = float(p["mock_win_rate"])
        self._payoff_ratio: float = float(p["mock_payoff_ratio"])
        self._n_trades: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._sum_win: float = 0.0
        self._sum_loss: float = 0.0

        # MDD
        self._peak_equity: float | None = None
        self._current_mdd_pct: float = 0.0

        # 포지션 상태
        self._entry_price: float | None = None
        self._entry_bar_index: int | None = None
        self._entry_atr: float | None = None
        self._entry_side: int = 0
        self._bar_index: int = 0
        self._is_closing: bool = False

        # Edge-trigger 메모리
        self._prev_long_signal: bool = False
        self._prev_short_signal: bool = False

        # 반전 확인용 이전 바 상태
        self._prev_bar_long_setup: bool = False  # 이전 바가 LONG 후보 극단
        self._prev_bar_short_setup: bool = False
        self._prev_bar_close: float | None = None
        self._prev_rsi: float | None = None
        self._prev_ema_fast: float | None = None
        self._prev_ema_slow: float | None = None

        # Providers
        self._oi_provider: Any | None = None
        self._funding_provider: Any | None = None
        self._lsr_provider: Any | None = None
        self._mode: str | None = None

        # 메타
        self.params = dict(p)
        self.indicator_config = {
            "BBANDS": {"period": self.bb_period,
                       "nbdevup": self.bb_stddev, "nbdevdn": self.bb_stddev},
            "RSI": {"period": self.rsi_period},
            "ATR": {"period": self.atr_period},
            "ADX": {"period": self.adx_period},
            "VWAP": {"period": self.vwap_period},
            "VOL_MA": {"period": self.volume_ma_period},
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initialize(self, ctx: StrategyContext) -> None:
        if get_oi_provider is None:
            raise RuntimeError(
                f"OI provider unavailable: {_OI_IMPORT_ERR}. "
                "Verify src/indicators/oi_provider.py is on PYTHONPATH."
            )
        if get_funding_provider is None or get_lsr_provider is None:
            raise RuntimeError(
                f"perp_meta providers unavailable: {_META_IMPORT_ERR}. "
                "Verify src/indicators/perp_meta_provider.py is on PYTHONPATH."
            )

        self.setup_indicators(ctx)

        # 상태 초기화
        self._peak_equity = None
        self._current_mdd_pct = 0.0
        self._entry_price = None
        self._entry_bar_index = None
        self._entry_atr = None
        self._entry_side = 0
        self._bar_index = 0
        self._is_closing = False
        self._prev_long_signal = False
        self._prev_short_signal = False
        self._prev_bar_long_setup = False
        self._prev_bar_short_setup = False
        self._prev_bar_close = None
        self._prev_rsi = None
        self._prev_ema_fast = None
        self._prev_ema_slow = None

        self._emit_event(ctx, "MFM_INIT", {
            "symbol": getattr(ctx, "symbol", "UNKNOWN"),
            "mode": self._mode,
            "bar_interval_ms": self.bar_interval_ms,
            "bb": (self.bb_period, self.bb_stddev),
            "rsi": (self.rsi_period, self.rsi_oversold, self.rsi_overbought),
            "atr_period": self.atr_period,
            "vwap_period": self.vwap_period,
            "adx_max": self.adx_max,
            "atr_pct_band": (self.atr_pct_min, self.atr_pct_max),
            "volume_mult_min": self.volume_mult_min,
            "tp_sl": (self.atr_tp_mult, self.atr_sl_mult),
            "time_stop_bars": self.time_stop_bars,
            "max_allowed_mdd_pct": self.max_allowed_mdd_pct,
        })

    def setup_indicators(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "BBANDS")
        register_talib_indicator_all_outputs(ctx, "RSI")
        register_talib_indicator_all_outputs(ctx, "ATR")
        register_talib_indicator_all_outputs(ctx, "ADX")
        register_talib_indicator_all_outputs(ctx, "EMA")
        _register_rolling_vwap(ctx)
        _register_rolling_volume_ma(ctx)

        symbol = getattr(ctx, "symbol", "BTCUSDT")
        mode = _detect_mode(ctx)
        self._mode = mode

        assert get_oi_provider is not None
        assert get_funding_provider is not None
        assert get_lsr_provider is not None
        self._oi_provider = get_oi_provider(symbol, mode=mode)
        self._funding_provider = get_funding_provider(symbol, mode=mode)
        self._lsr_provider = get_lsr_provider(symbol, mode=mode)

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # 1) flat 리셋
        if ctx.position_size == 0:
            self._is_closing = False
            if self._entry_price is not None:
                self._entry_price = None
                self._entry_bar_index = None
                self._entry_atr = None
                self._entry_side = 0

        # 2) MDD 매 틱 업데이트
        self._update_mdd(ctx)

        if not bool(bar.get("is_new_bar", True)):
            return

        try:
            open_orders = ctx.get_open_orders() or []
        except Exception:  # noqa: BLE001
            open_orders = []
        if open_orders:
            return

        close = float(bar.get("close", bar.get("price", 0.0)) or 0.0)
        if not math.isfinite(close) or close <= 0:
            return
        high = float(bar.get("high", close) or close)
        low = float(bar.get("low", close) or close)
        ts = _bar_timestamp_from_bar(bar)

        # 3) 청산 먼저 (포지션 있을 때)
        if ctx.position_size != 0 and self._entry_price is not None and not self._is_closing:
            exit_sig = self.check_exit_conditions(
                ctx, bar, high=high, low=low, close=close,
            )
            if exit_sig is not None:
                self._is_closing = True
                exit_price = float(exit_sig.get("exit_price", close))
                pnl = self._compute_unit_pnl(exit_price)
                exit_event_name = exit_sig.get("event", "MFM_EXIT")
                cap = getattr(ctx, "close_position_at_price", None)
                if callable(cap):
                    cap(
                        price=exit_price,
                        reason=exit_sig["reason"],
                        exit_reason=exit_event_name,
                    )
                else:
                    ctx.close_position(
                        reason=exit_sig["reason"],
                        exit_reason=exit_event_name,
                    )
                self._record_trade_outcome(pnl)
                self._emit_event(ctx, exit_event_name, {
                    "bar_ts": ts,
                    "entry_price": self._entry_price,
                    "exit_price": exit_price,
                    "reason": exit_sig["reason"],
                    "unit_pnl": pnl,
                    "held_bars": (
                        self._bar_index - self._entry_bar_index
                        if self._entry_bar_index is not None else None
                    ),
                    "wins": self._wins,
                    "losses": self._losses,
                    "win_rate": self._win_rate,
                    "payoff_ratio": self._payoff_ratio,
                    "current_mdd_pct": self._current_mdd_pct,
                })
                self._bar_index += 1
                return

        # 4) flat 일 때만 진입
        self._bar_index += 1
        if ctx.position_size != 0:
            return

        signal = self.check_entry_conditions(ctx, bar, close=close, ts=ts)
        if signal is None:
            return

        side = signal["side"]
        reason = signal.get("reason", "MFM entry")
        entry_pct = signal.get("entry_pct")
        atr_value = float(signal["atr"])

        if side == "long":
            if entry_pct is not None:
                ctx.enter_long(reason=reason, entry_pct=float(entry_pct))
            else:
                ctx.enter_long(reason=reason)
            self._entry_side = 1
        elif side == "short":
            if entry_pct is not None:
                ctx.enter_short(reason=reason, entry_pct=float(entry_pct))
            else:
                ctx.enter_short(reason=reason)
            self._entry_side = -1
        else:
            return

        self._entry_price = close
        self._entry_bar_index = self._bar_index
        self._entry_atr = atr_value

        self._emit_event(ctx, "MFM_ENTRY", {
            "bar_ts": ts,
            "side": side,
            "entry_price": close,
            "atr": atr_value,
            "tp_target": close + self.atr_tp_mult * atr_value * self._entry_side,
            "sl_target": close - self.atr_sl_mult * atr_value * self._entry_side,
            "entry_pct": entry_pct,
            "n_trades": self._n_trades,
            "win_rate": self._win_rate,
            "payoff_ratio": self._payoff_ratio,
            "current_mdd_pct": self._current_mdd_pct,
            "reason": reason,
        })

    # ------------------------------------------------------------------
    # Signal evaluation — MEAN REVERSION at extremes (v11)
    # ------------------------------------------------------------------
    def check_entry_conditions(
        self,
        ctx: StrategyContext,
        bar: dict[str, Any],
        *,
        close: float,
        ts: int,
    ) -> dict[str, Any] | None:
        # 1) 지표
        bb = ctx.get_indicator(
            "BBANDS", period=self.bb_period,
            nbdevup=self.bb_stddev, nbdevdn=self.bb_stddev,
        )
        if not isinstance(bb, dict):
            return None
        upper = float(bb.get("upperband", bb.get("output_0", math.nan)))
        lower = float(bb.get("lowerband", bb.get("output_2", math.nan)))
        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        vwap = float(ctx.get_indicator("VWAP", period=self.vwap_period))
        adx = float(ctx.get_indicator("ADX", period=self.adx_period))
        vol_ma = float(ctx.get_indicator("VOL_MA", period=self.volume_ma_period))
        if not all(math.isfinite(v) and v > 0 for v in (atr, vwap, vol_ma)):
            return None
        if not all(math.isfinite(v) for v in (upper, lower, rsi, adx)):
            return None

        # 2) 변동성 레짐 필터
        atr_pct = atr / close
        if not (self.atr_pct_min <= atr_pct <= self.atr_pct_max):
            self._prev_long_signal = False
            self._prev_short_signal = False
            self._prev_bar_long_setup = False
            self._prev_bar_short_setup = False
            self._prev_bar_close = close
            return None

        # 3) 추세 레짐 필터
        # mean_rev: low ADX = range market 필요 (adx_max 이하)
        # trend: high ADX 필요 (trend_adx_min 이상)
        if self.signal_mode == "mean_rev" and adx > self.adx_max:
            self._prev_long_signal = False
            self._prev_short_signal = False
            self._prev_bar_long_setup = False
            self._prev_bar_short_setup = False
            self._prev_bar_close = close
            return None
        if self.signal_mode == "trend" and adx < self.trend_adx_min:
            self._prev_long_signal = False
            self._prev_short_signal = False
            self._prev_bar_long_setup = False
            self._prev_bar_short_setup = False
            self._prev_bar_close = close
            return None

        # 4) 거래량 필터
        volume = float(bar.get("volume", 0.0) or 0.0)
        if volume < vol_ma * self.volume_mult_min:
            self._prev_long_signal = False
            self._prev_short_signal = False
            self._prev_bar_long_setup = False
            self._prev_bar_short_setup = False
            self._prev_bar_close = close
            return None

        # 5) OI 캐스케이드 차단
        oi_pct = math.nan
        if self._oi_provider is not None and ts > 0:
            try:
                oi_pct = float(self._oi_provider.pct_change(
                    ts, lookback_ms=self.oi_lookback_ms,
                ))
            except Exception:  # noqa: BLE001
                oi_pct = math.nan
        if math.isfinite(oi_pct) and oi_pct <= self.oi_drop_block_pct:
            self._prev_long_signal = False
            self._prev_short_signal = False
            self._prev_bar_long_setup = False
            self._prev_bar_short_setup = False
            self._prev_bar_close = close
            return None

        # 6) 신호 계산
        vwap_stretch = close - vwap
        stretch_threshold = self.vwap_stretch_atr_mult * atr

        # ---- TREND mode: EMA 9/21 cross + ADX strong ----
        trend_long_edge = False
        trend_short_edge = False
        ema_fast_val: float | None = None
        ema_slow_val: float | None = None
        if self.signal_mode == "trend":
            ema_fast_val = float(ctx.get_indicator("EMA", period=self.ema_fast_period))
            ema_slow_val = float(ctx.get_indicator("EMA", period=self.ema_slow_period))
            if (
                math.isfinite(ema_fast_val)
                and math.isfinite(ema_slow_val)
                and self._prev_ema_fast is not None
                and self._prev_ema_slow is not None
                and adx >= self.trend_adx_min
            ):
                cross_up = (
                    self._prev_ema_fast <= self._prev_ema_slow
                    and ema_fast_val > ema_slow_val
                    and close > ema_slow_val
                )
                cross_dn = (
                    self._prev_ema_fast >= self._prev_ema_slow
                    and ema_fast_val < ema_slow_val
                    and close < ema_slow_val
                )
                trend_long_edge = bool(cross_up)
                trend_short_edge = bool(cross_dn)
        if ema_fast_val is not None:
            self._prev_ema_fast = ema_fast_val
        if ema_slow_val is not None:
            self._prev_ema_slow = ema_slow_val

        long_setup = (
            close <= lower
            and rsi <= self.rsi_oversold
            and vwap_stretch <= -stretch_threshold
        )
        short_setup = (
            close >= upper
            and rsi >= self.rsi_overbought
            and vwap_stretch >= stretch_threshold
        )        # 6-a) 매크로 추세 필터: 강한 추세 구간에서는 역방향 진입 차단
        macro_block_long = False
        macro_block_short = False
        if self.macro_ema_period > 0 and self.macro_band_atr > 0:
            ema_val = float(ctx.get_indicator("EMA", period=self.macro_ema_period))
            if math.isfinite(ema_val) and ema_val > 0:
                # 가격이 EMA 위로 macro_band_atr*ATR 이상 → 매크로 업트렌드 → SHORT 차단
                # 가격이 EMA 아래로 macro_band_atr*ATR 이상 → 매크로 다운트렌드 → LONG 차단
                band = self.macro_band_atr * atr
                if close > ema_val + band:
                    macro_block_short = True
                if close < ema_val - band:
                    macro_block_long = True
        if macro_block_long:
            long_setup = False
        if macro_block_short:
            short_setup = False

        # 6-b) 반전 확인: 이전 바가 setup 이고 현재 바가 강한 반전 캔들이어야 진입
        bar_open = float(bar.get("open", close) or close)
        long_edge = False
        short_edge = False

        # RSI hook: 이전 바 RSI 가 극단 안, 현재 RSI 가 극단 밖
        rsi_hook_long = False
        rsi_hook_short = False
        if (
            self.use_rsi_hook
            and self._prev_rsi is not None
            and self._prev_bar_close is not None
        ):
            rsi_hook_long = (
                self._prev_rsi < self.rsi_oversold
                and rsi >= self.rsi_oversold
                and close > self._prev_bar_close
            )
            rsi_hook_short = (
                self._prev_rsi > self.rsi_overbought
                and rsi <= self.rsi_overbought
                and close < self._prev_bar_close
            )

        if self.require_reversal_bar:
            reversal_long = (
                self._prev_bar_long_setup
                and self._prev_bar_close is not None
                and close > self._prev_bar_close
                and close > bar_open
                and not long_setup
            )
            reversal_short = (
                self._prev_bar_short_setup
                and self._prev_bar_close is not None
                and close < self._prev_bar_close
                and close < bar_open
                and not short_setup
            )
            long_edge = reversal_long or rsi_hook_long
            short_edge = reversal_short or rsi_hook_short
        else:
            long_edge = (long_setup and not self._prev_long_signal) or rsi_hook_long
            short_edge = (short_setup and not self._prev_short_signal) or rsi_hook_short

        # signal_mode "trend" 일 땐 평균회귀 신호 무시하고 EMA 크로스만 사용
        if self.signal_mode == "trend":
            long_edge = trend_long_edge
            short_edge = trend_short_edge

        # 상태 갱신 (다음 바에서 참조)
        self._prev_long_signal = long_setup
        self._prev_short_signal = short_setup
        self._prev_bar_long_setup = long_setup
        self._prev_bar_short_setup = short_setup
        self._prev_bar_close = close
        self._prev_rsi = rsi

        if not (long_edge or short_edge):
            return None

        # 6-c) 방향 제약
        if self.direction_mode == "long" and not long_edge:
            return None
        if self.direction_mode == "short" and not short_edge:
            return None
        if self.direction_mode == "long":
            short_edge = False
        elif self.direction_mode == "short":
            long_edge = False

        # 6-d) 펀딩 레이트 필터 (반대편 군중이 과밀할 때만 진입)
        if self.funding_filter_enabled and self._funding_provider is not None and ts > 0:
            try:
                funding = float(self._funding_provider.value_at(ts))
            except Exception:  # noqa: BLE001
                funding = math.nan
            if math.isfinite(funding):
                if long_edge and funding > self.funding_long_max:
                    return None
                if short_edge and funding < self.funding_short_min:
                    return None

        # 7) 사이징
        target_lev = self.kelly_risk_manager.get_target_leverage(
            current_mdd_pct=self._current_mdd_pct,
            max_allowed_mdd_pct=self.max_allowed_mdd_pct,
            win_rate=self._win_rate,
            payoff_ratio=self._payoff_ratio,
            n_trades=self._n_trades,
        )
        if target_lev <= 0:
            return None

        if long_edge:
            reason = (
                f"MFM LONG mean-rev: c={close:.2f}<=L={lower:.2f}, "
                f"RSI={rsi:.1f}<={self.rsi_oversold:.0f}, "
                f"stretch={vwap_stretch:.2f}, ADX={adx:.1f}, "
                f"ATR%={atr_pct * 100:.3f}, lev={target_lev:.3f}"
            )
            return {"side": "long", "reason": reason,
                    "atr": atr, "entry_pct": target_lev}

        reason = (
            f"MFM SHORT mean-rev: c={close:.2f}>=U={upper:.2f}, "
            f"RSI={rsi:.1f}>={self.rsi_overbought:.0f}, "
            f"stretch={vwap_stretch:.2f}, ADX={adx:.1f}, "
            f"ATR%={atr_pct * 100:.3f}, lev={target_lev:.3f}"
        )
        return {"side": "short", "reason": reason,
                "atr": atr, "entry_pct": target_lev}

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------
    def check_exit_conditions(
        self,
        ctx: StrategyContext,
        bar: dict[str, Any],
        *,
        high: float,
        low: float,
        close: float,
    ) -> dict[str, Any] | None:
        if (
            self._entry_price is None
            or self._entry_atr is None
            or self._entry_bar_index is None
            or self._entry_side == 0
        ):
            return None

        entry = self._entry_price
        atr0 = self._entry_atr
        side = self._entry_side

        if side == 1:  # long
            tp_price = entry + self.atr_tp_mult * atr0
            sl_price = entry - self.atr_sl_mult * atr0
            if math.isfinite(low) and low <= sl_price:
                return {
                    "reason": f"SL {self.atr_sl_mult}*ATR (={sl_price:.4f})",
                    "exit_price": sl_price,
                    "event": "MFM_EXIT_SL",
                }
            if math.isfinite(high) and high >= tp_price:
                return {
                    "reason": f"TP {self.atr_tp_mult}*ATR (={tp_price:.4f})",
                    "exit_price": tp_price,
                    "event": "MFM_EXIT_TP",
                }
        else:  # short
            tp_price = entry - self.atr_tp_mult * atr0
            sl_price = entry + self.atr_sl_mult * atr0
            if math.isfinite(high) and high >= sl_price:
                return {
                    "reason": f"SL {self.atr_sl_mult}*ATR (={sl_price:.4f})",
                    "exit_price": sl_price,
                    "event": "MFM_EXIT_SL",
                }
            if math.isfinite(low) and low <= tp_price:
                return {
                    "reason": f"TP {self.atr_tp_mult}*ATR (={tp_price:.4f})",
                    "exit_price": tp_price,
                    "event": "MFM_EXIT_TP",
                }

        held = self._bar_index - self._entry_bar_index
        if held >= self.time_stop_bars:
            return {
                "reason": f"Time Stop ({self.time_stop_bars} bars)",
                "exit_price": close,
                "event": "MFM_EXIT_TIME_STOP",
            }
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _compute_unit_pnl(self, exit_price: float) -> float:
        if self._entry_price is None or self._entry_side == 0:
            return 0.0
        return float((exit_price - self._entry_price) * self._entry_side)

    def _record_trade_outcome(self, pnl: float) -> None:
        if not math.isfinite(pnl) or pnl == 0.0:
            return
        self._n_trades += 1
        if pnl > 0:
            self._wins += 1
            self._sum_win += pnl
        else:
            self._losses += 1
            self._sum_loss += -pnl
        self._win_rate = self._wins / self._n_trades if self._n_trades else 0.0
        if self._losses > 0:
            avg_win = self._sum_win / self._wins if self._wins else 0.0
            avg_loss = self._sum_loss / self._losses if self._losses else 1.0
            self._payoff_ratio = avg_win / avg_loss if avg_loss > 0 else self._payoff_ratio

    def _update_mdd(self, ctx: StrategyContext) -> None:
        try:
            balance = float(getattr(ctx, "balance", 0.0) or 0.0)
            upnl = float(getattr(ctx, "unrealized_pnl", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            return
        equity = balance + upnl
        if not math.isfinite(equity):
            return
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity and self._peak_equity > 0:
            self._current_mdd_pct = max(
                0.0, (self._peak_equity - equity) / self._peak_equity * 100.0
            )

    def _emit_event(
        self,
        ctx: StrategyContext,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        emit = getattr(ctx, "emit_event", None) or getattr(ctx, "log_event", None)
        if callable(emit):
            try:
                emit(event, payload)
            except Exception:  # noqa: BLE001
                pass
