"""다중 팩터 미시구조 전략 (Multi-Factor Microstructure Strategy).

설계 개요:
- **추세 필터 (VWAP)**: 가격과 VWAP의 상대 위치로 진입 방향을 거른다.
- **미시구조 알파 (OI 5봉 변화)**: 5봉 구간 OI가 급감(<0)하면서 단기 가격
  방향성이 반전될 때 진입 신호를 생성한다. (= 강제 청산성 OI 감소 +
  방향 전환 = 유의미한 미시구조 시그널)
- **포지션 사이징 (Dynamic Kelly + MDD 페널티)**: 펀딩비가 양수 극단이고
  LSR이 높을 때(롱 과밀) 숏 진입이 발생하면, ``DynamicKellyRiskManager`` 에
  현재 승률 / 손익비 / 시스템 MDD 를 넘겨 최적 레버리지를 받아 시그널에
  싣는다. 일반 롱 진입에는 시스템 기본 사이징을 사용한다.
- **Triple Barrier 청산**:
    * Volatility Stop — 진입가 대비 ±(2 * ATR) 도달 시 청산.
    * Time Stop      — 15봉 경과 시 강제 청산(횡보장 / 펀딩비 누수 방어).

규격(llmtrader 컨벤션):
- ``scripts.strategies.*`` 모듈은 ``src/`` 를 sys.path 에 주입한 뒤
  ``strategy.base.Strategy`` 를 상속한다. (현 코드베이스에는
  ``IndicatorStrategy`` 클래스가 별도로 존재하지 않으며, 인디케이터 기반
  전략은 ``Strategy`` 를 상속하고 ``setup_indicators`` /
  ``check_entry_conditions`` / ``check_exit_conditions`` 헬퍼 메서드 패턴을
  사용한다. ``indicator_strategy_template.py`` 가 그 규격이다.)
- 지표는 ``ctx.register_indicator`` / ``ctx.get_indicator`` 를 거치고,
  데이터 시계열은 ``indicators.oi_provider`` / ``indicators.perp_meta_provider``
  를 사용한다.
- 주문은 ``ctx.enter_long`` / ``ctx.enter_short`` / ``ctx.close_position`` 으로만
  발행하며, ``entry_pct`` 인자를 통해 Kelly가 산출한 레버리지를 주입한다.
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
# TA-Lib indicator registration (공유 패턴; bb_rsi_oi_meanrev_strategy 와 동일).
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
    """TA-Lib builtin 인디케이터를 ctx에 등록한다.

    multi-output 결과는 dict로 반환하고, single-output 결과는 float로 반환한다.
    """
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
    """롤링 VWAP를 ``VWAP`` 이름으로 ctx에 등록한다.

    TA-Lib에는 VWAP가 없으므로 hybrid_regime_scalping_strategy 와 동일한 구현.
    typical price * volume 합 / volume 합, 최근 ``period`` 봉 기준.
    """
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
    """ctx 타입으로 backtest / live 모드를 추정한다."""
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
    # 지표 파라미터
    "vwap_period": 60,
    "atr_period": 14,
    # 미시구조 (OI / funding / LSR)
    "oi_lookback_bars": 5,
    "bar_interval_ms": 15 * 60 * 1000,  # 15m 기본
    "funding_extreme_threshold": 0.0001,  # 0.01% / 8h
    "lsr_high_threshold": 2.0,             # 글로벌 LSR > 2.0 = 롱 과밀
    # Triple Barrier
    "atr_stop_mult": 2.0,
    "time_stop_bars": 15,
    # Dynamic Kelly
    "kelly_min_trades": 30,
    "kelly_burn_in_leverage": 0.01,
    "kelly_fraction": 0.5,
    "kelly_max_leverage": 1.0,
    "max_allowed_mdd_pct": 20.0,
    # Mock 통계 시드 (실거래 이전 콜드 스타트 값; 번인이 끝나면 실시간 통계로 교체)
    "mock_win_rate": 0.5,
    "mock_payoff_ratio": 1.0,
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "vwap_period", "type": "int", "min": 5, "max": 500, "label": "VWAP period"},
    {"name": "atr_period", "type": "int", "min": 2, "max": 100, "label": "ATR period"},
    {"name": "oi_lookback_bars", "type": "int", "min": 1, "max": 200,
     "label": "OI lookback (bars)"},
    {"name": "bar_interval_ms", "type": "int", "min": 60_000,
     "max": 24 * 3600_000, "label": "Bar interval (ms)"},
    {"name": "funding_extreme_threshold", "type": "float",
     "min": 0.0, "max": 0.01, "step": 0.00001,
     "label": "Funding extreme threshold (8h)"},
    {"name": "lsr_high_threshold", "type": "float",
     "min": 1.0, "max": 10.0, "step": 0.1, "label": "LSR high threshold"},
    {"name": "atr_stop_mult", "type": "float", "min": 0.5, "max": 10.0, "step": 0.1,
     "label": "Vol stop ATR multiplier"},
    {"name": "time_stop_bars", "type": "int", "min": 1, "max": 200,
     "label": "Time stop (bars)"},
    {"name": "kelly_min_trades", "type": "int", "min": 0, "max": 1000,
     "label": "Kelly burn-in threshold"},
    {"name": "kelly_burn_in_leverage", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.001, "label": "Burn-in leverage"},
    {"name": "kelly_fraction", "type": "float",
     "min": 0.05, "max": 1.0, "step": 0.05, "label": "Kelly fraction"},
    {"name": "kelly_max_leverage", "type": "float",
     "min": 0.0, "max": 5.0, "step": 0.05, "label": "Kelly max leverage"},
    {"name": "max_allowed_mdd_pct", "type": "float",
     "min": 1.0, "max": 90.0, "step": 0.5, "label": "Max allowed MDD (%)"},
]


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class MultiFactorMicrostructureStrategy(Strategy):
    """다중 팩터 미시구조 전략.

    파이프라인:
        on_bar → check_exit_conditions (in-position) → check_entry_conditions (flat)

    Kelly 사이징 경로(롱 과밀 → 숏)에서만 ``entry_pct`` 가 시그널에 실리고,
    그 외 진입은 시스템 기본 사이징을 사용한다.
    """

    def __init__(self, **kwargs: Any) -> None:
        """전략 초기화.

        Args:
            **kwargs: ``STRATEGY_PARAMS`` 의 키를 임의로 덮어쓸 수 있다.
        """
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}

        # ---- 지표 파라미터 ----
        self.vwap_period = int(p["vwap_period"])
        self.atr_period = int(p["atr_period"])

        # ---- 미시구조 파라미터 ----
        self.oi_lookback_bars = int(p["oi_lookback_bars"])
        self.bar_interval_ms = int(p["bar_interval_ms"])
        self.oi_lookback_ms = self.oi_lookback_bars * self.bar_interval_ms
        self.funding_extreme_threshold = float(p["funding_extreme_threshold"])
        self.lsr_high_threshold = float(p["lsr_high_threshold"])

        # ---- Triple Barrier ----
        self.atr_stop_mult = float(p["atr_stop_mult"])
        self.time_stop_bars = int(p["time_stop_bars"])

        # ---- Dynamic Kelly 위험 관리자 (Task 1) ----
        self.kelly_risk_manager: DynamicKellyRiskManager = DynamicKellyRiskManager(
            min_trades_required=int(p["kelly_min_trades"]),
            burn_in_leverage=float(p["kelly_burn_in_leverage"]),
            kelly_fraction=float(p["kelly_fraction"]),
            max_leverage=float(p["kelly_max_leverage"]),
        )
        self.max_allowed_mdd_pct = float(p["max_allowed_mdd_pct"])

        # ---- 승률 / 손익비 추적용 내부 변수 (mock) ----
        # 실제 거래가 누적되기 전까지 사용할 시드값.
        self._win_rate: float = float(p["mock_win_rate"])
        self._payoff_ratio: float = float(p["mock_payoff_ratio"])
        self._n_trades: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._sum_win: float = 0.0  # 누적 승리 PnL (절대값)
        self._sum_loss: float = 0.0  # 누적 손실 PnL (절대값)

        # ---- MDD 추적 ----
        self._peak_equity: float | None = None
        self._current_mdd_pct: float = 0.0

        # ---- 포지션 / 바 상태 ----
        self._entry_price: float | None = None
        self._entry_bar_index: int | None = None
        self._entry_atr: float | None = None
        self._entry_side: int = 0  # +1=long, -1=short, 0=flat
        self._bar_index: int = 0
        self._is_closing: bool = False

        # ---- 단기 방향성 반전 검출용 (직전 2개 close) ----
        self._prev_close: float | None = None
        self._prev_prev_close: float | None = None

        # ---- 외부 데이터 프로바이더 ----
        self._oi_provider: Any | None = None
        self._funding_provider: Any | None = None
        self._lsr_provider: Any | None = None
        self._mode: str | None = None

        # ---- 메타 ----
        self.params = dict(p)
        self.indicator_config = {
            "VWAP": {"period": self.vwap_period},
            "ATR": {"period": self.atr_period},
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initialize(self, ctx: StrategyContext) -> None:
        """전략 시작 시 1회 호출. 지표 등록 + 프로바이더 결선."""
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

        # 상태 초기화 (재시작 안전성).
        self._peak_equity = None
        self._current_mdd_pct = 0.0
        self._entry_price = None
        self._entry_bar_index = None
        self._entry_atr = None
        self._entry_side = 0
        self._bar_index = 0
        self._is_closing = False
        self._prev_close = None
        self._prev_prev_close = None

        self._emit_event(ctx, "MFM_INIT", {
            "symbol": getattr(ctx, "symbol", "UNKNOWN"),
            "mode": self._mode,
            "vwap_period": self.vwap_period,
            "atr_period": self.atr_period,
            "oi_lookback_bars": self.oi_lookback_bars,
            "bar_interval_ms": self.bar_interval_ms,
            "funding_extreme_threshold": self.funding_extreme_threshold,
            "lsr_high_threshold": self.lsr_high_threshold,
            "atr_stop_mult": self.atr_stop_mult,
            "time_stop_bars": self.time_stop_bars,
            "kelly_min_trades": self.kelly_risk_manager.min_trades_required,
            "kelly_fraction": self.kelly_risk_manager.kelly_fraction,
            "max_allowed_mdd_pct": self.max_allowed_mdd_pct,
        })

    def setup_indicators(self, ctx: StrategyContext) -> None:
        """지표 / 데이터 프로바이더 일괄 등록.

        - ``ctx.register_indicator`` 로 ``VWAP`` (커스텀 롤링), ``ATR`` (TA-Lib).
        - ``oi_provider`` / ``perp_meta_provider`` 에서 OI / funding / LSR
          프로바이더를 인스턴스화.
        """
        # VWAP는 TA-Lib에 없으므로 커스텀.
        _register_rolling_vwap(ctx)
        # ATR은 TA-Lib builtin.
        register_talib_indicator_all_outputs(ctx, "ATR")

        symbol = getattr(ctx, "symbol", "BTCUSDT")
        mode = _detect_mode(ctx)
        self._mode = mode

        assert get_oi_provider is not None  # for type checkers; initialize() validated
        assert get_funding_provider is not None
        assert get_lsr_provider is not None
        self._oi_provider = get_oi_provider(symbol, mode=mode)
        self._funding_provider = get_funding_provider(symbol, mode=mode)
        self._lsr_provider = get_lsr_provider(symbol, mode=mode)

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        """매 바 진입점.

        흐름:
            1) 플랫 진입 시 진입 상태 리셋
            2) 매 틱마다 MDD 업데이트
            3) ``is_new_bar`` 인 봉만 신호 평가 — 백테스트 stoploss 시뮬과 호환
            4) 청산 우선 → 진입 (둘 다 같은 봉에서 트리거되지 않도록 보호)
        """
        # 1) 플랫이면 진입 추적 리셋.
        if ctx.position_size == 0:
            self._is_closing = False
            if self._entry_price is not None:
                # 외부 청산(예: 사용자 수동) 흔적은 통계엔 반영하지 않는다 — 모르는 PnL을 추정하지 않음.
                self._entry_price = None
                self._entry_bar_index = None
                self._entry_atr = None
                self._entry_side = 0

        # 2) MDD는 매 틱마다 (intra-bar 가격 변동도 반영).
        self._update_mdd(ctx)

        is_new_bar = bool(bar.get("is_new_bar", True))
        if not is_new_bar:
            return

        # 라이브 중복 주문 가드.
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

        # 3) 청산 우선 — 이미 포지션이 있으면 진입 평가 안 함.
        if ctx.position_size != 0 and self._entry_price is not None and not self._is_closing:
            exit_sig = self.check_exit_conditions(ctx, bar, high=high, low=low, close=close)
            if exit_sig is not None:
                self._is_closing = True
                exit_price = float(exit_sig.get("exit_price", close))
                pnl = self._compute_unit_pnl(exit_price)
                ctx.close_position(reason=exit_sig["reason"])
                self._record_trade_outcome(pnl)
                self._emit_event(ctx, exit_sig.get("event", "MFM_EXIT"), {
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
                # 봉 단위 카운터는 항상 전진.
                self._bar_index += 1
                self._update_direction_memory(close)
                return

        # 4) flat 일 때만 진입 평가.
        self._bar_index += 1
        if ctx.position_size != 0:
            # 포지션은 있는데 entry_price 가 없는 비정상 상태 — 추적만 유지.
            self._update_direction_memory(close)
            return

        signal = self.check_entry_conditions(ctx, bar, close=close, ts=ts)
        if signal is not None:
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
                self._update_direction_memory(close)
                return

            self._entry_price = close
            self._entry_bar_index = self._bar_index
            self._entry_atr = atr_value

            self._emit_event(ctx, "MFM_ENTRY", {
                "bar_ts": ts,
                "side": side,
                "entry_price": close,
                "atr": atr_value,
                "vol_stop_upper": close + self.atr_stop_mult * atr_value,
                "vol_stop_lower": close - self.atr_stop_mult * atr_value,
                "entry_pct": entry_pct,
                "n_trades": self._n_trades,
                "win_rate": self._win_rate,
                "payoff_ratio": self._payoff_ratio,
                "current_mdd_pct": self._current_mdd_pct,
                "reason": reason,
            })

        self._update_direction_memory(close)

    # ------------------------------------------------------------------
    # Signal evaluation
    # ------------------------------------------------------------------
    def check_entry_conditions(
        self,
        ctx: StrategyContext,
        bar: dict[str, Any],
        *,
        close: float,
        ts: int,
    ) -> dict[str, Any] | None:
        """진입 조건 평가. 신호가 있으면 dict, 없으면 None.

        반환 dict 형식 (시그널 컨테이너):
            {
                "side": "long" | "short",
                "reason": str,
                "atr": float,                  # 진입 봉의 ATR (Triple Barrier용)
                "entry_pct": float | None,     # Kelly 산출 레버리지 (없으면 기본 사이징)
            }

        Args:
            ctx: 전략 컨텍스트.
            bar: 새 봉 데이터.
            close: 현재 봉 종가.
            ts: 봉 타임스탬프 (ms).

        Returns:
            진입 시그널 dict 또는 ``None``.
        """
        # --- 지표 ---
        vwap = float(ctx.get_indicator("VWAP", period=self.vwap_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        if not (math.isfinite(vwap) and math.isfinite(atr) and atr > 0):
            return None

        # --- 단기 방향성 반전 ---
        # 직전 2개 close 가 있어야 reversal 판정 가능.
        prev = self._prev_close
        prev_prev = self._prev_prev_close
        if prev is None or prev_prev is None:
            return None
        prev_direction = _sign(prev - prev_prev)
        curr_direction = _sign(close - prev)
        reversal_up = prev_direction < 0 < curr_direction
        reversal_down = prev_direction > 0 > curr_direction

        # --- 미시구조 알파: OI 5봉 변화 < 0 (강제청산성 OI 감소) ---
        oi_pct = math.nan
        if self._oi_provider is not None and ts > 0:
            try:
                oi_pct = float(self._oi_provider.pct_change(
                    ts, lookback_ms=self.oi_lookback_ms,
                ))
            except Exception:  # noqa: BLE001
                oi_pct = math.nan
        if not math.isfinite(oi_pct) or oi_pct >= 0:
            # OI 데이터 없거나 감소가 아님 → 미시구조 알파 부재.
            return None

        # --- 추세 필터 (VWAP) ---
        above_vwap = close > vwap
        below_vwap = close < vwap

        # --- 펀딩비 / LSR (롱 과밀 검출용) ---
        funding = self._safe_value_at(self._funding_provider, ts)
        lsr = self._safe_value_at(self._lsr_provider, ts)
        crowded_long = (
            math.isfinite(funding)
            and funding > self.funding_extreme_threshold
            and math.isfinite(lsr)
            and lsr > self.lsr_high_threshold
        )

        # --- 1) 롱 과밀 → 숏 (Kelly 사이징 경로) ---
        # 조건: OI 급감 + VWAP 아래 + 단기 방향 반전(하향) + 펀딩 극단 + LSR 높음.
        if reversal_down and below_vwap and crowded_long:
            target_lev = self.kelly_risk_manager.get_target_leverage(
                current_mdd_pct=self._current_mdd_pct,
                max_allowed_mdd_pct=self.max_allowed_mdd_pct,
                win_rate=self._win_rate,
                payoff_ratio=self._payoff_ratio,
                n_trades=self._n_trades,
            )
            reason = (
                f"MFM short (crowded long): close={close:.2f}<VWAP={vwap:.2f}, "
                f"OI{self.oi_lookback_bars}b={oi_pct * 100:.2f}%, "
                f"funding={funding * 100:.4f}%, LSR={lsr:.2f}, "
                f"lev={target_lev:.4f}"
            )
            return {
                "side": "short",
                "reason": reason,
                "atr": atr,
                "entry_pct": target_lev,
            }

        # --- 2) OI 급감 + VWAP 위 + 단기 방향 반전(상향) → 롱 (기본 사이징) ---
        if reversal_up and above_vwap:
            reason = (
                f"MFM long: close={close:.2f}>VWAP={vwap:.2f}, "
                f"OI{self.oi_lookback_bars}b={oi_pct * 100:.2f}%"
            )
            return {
                "side": "long",
                "reason": reason,
                "atr": atr,
                "entry_pct": None,
            }

        return None

    def check_exit_conditions(
        self,
        ctx: StrategyContext,
        bar: dict[str, Any],
        *,
        high: float,
        low: float,
        close: float,
    ) -> dict[str, Any] | None:
        """Triple Barrier 청산 조건 평가.

        Barrier:
            1) Volatility Stop — ``|price - entry| >= atr_stop_mult * ATR_at_entry``
               (롱은 상승/하락 양방향 모두 청산, 숏도 동일. 변동성 자체가 한도를
               초과하면 시그널이 무효라고 본다.)
            2) Time Stop — 진입 후 ``time_stop_bars`` 봉 경과 시 강제 청산.

        반환:
            ``{"reason": str, "exit_price": float, "event": str}`` 또는 ``None``.
        """
        if (
            self._entry_price is None
            or self._entry_atr is None
            or self._entry_bar_index is None
        ):
            return None

        entry = self._entry_price
        atr0 = self._entry_atr
        upper = entry + self.atr_stop_mult * atr0
        lower = entry - self.atr_stop_mult * atr0

        # 1) Volatility stop: high가 상단 또는 low가 하단을 터치하면 즉시 청산.
        if math.isfinite(high) and high >= upper:
            return {
                "reason": (
                    f"Volatility Stop (+{self.atr_stop_mult}*ATR={atr0:.4f}, "
                    f"upper={upper:.4f})"
                ),
                "exit_price": upper,
                "event": "MFM_EXIT_VOL_STOP",
            }
        if math.isfinite(low) and low <= lower:
            return {
                "reason": (
                    f"Volatility Stop (-{self.atr_stop_mult}*ATR={atr0:.4f}, "
                    f"lower={lower:.4f})"
                ),
                "exit_price": lower,
                "event": "MFM_EXIT_VOL_STOP",
            }

        # 2) Time stop: 15봉 경과 시 close에 청산.
        held = self._bar_index - self._entry_bar_index
        if held >= self.time_stop_bars:
            return {
                "reason": f"Time Stop ({self.time_stop_bars} bars held)",
                "exit_price": close,
                "event": "MFM_EXIT_TIME_STOP",
            }

        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _safe_value_at(self, provider: Any, ts: int) -> float:
        """프로바이더에서 ``value_at`` 호출. 예외나 미설정 시 NaN."""
        if provider is None or ts <= 0:
            return math.nan
        try:
            return float(provider.value_at(int(ts)))
        except Exception:  # noqa: BLE001
            return math.nan

    def _update_direction_memory(self, close: float) -> None:
        """다음 봉에서 reversal 판정에 쓸 직전 2개 close 갱신."""
        self._prev_prev_close = self._prev_close
        self._prev_close = close

    def _compute_unit_pnl(self, exit_price: float) -> float:
        """단위 가격 PnL (방향 부호 포함). 청산 직전 상태에서만 호출.

        ``+값 = 승``, ``-값 = 패``. 절대 USDT 손익은 아니지만 승률·손익비
        통계용 부호와 상대 크기로 충분하다.
        """
        if self._entry_price is None or self._entry_side == 0:
            return 0.0
        return float((exit_price - self._entry_price) * self._entry_side)

    def _record_trade_outcome(self, pnl: float) -> None:
        """청산 시 승률 / 손익비 / 거래 수 누적 업데이트.

        Mock 통계지만, 한 번 거래가 쌓이기 시작하면 콜드 스타트 시드값을
        실시간 통계로 대체한다.
        """
        if not math.isfinite(pnl) or pnl == 0.0:
            # 0 PnL은 break-even 으로 표본에서 제외.
            return
        self._n_trades += 1
        if pnl > 0:
            self._wins += 1
            self._sum_win += pnl
        else:
            self._losses += 1
            self._sum_loss += -pnl  # 절대값 누적

        # 통계 갱신.
        self._win_rate = self._wins / self._n_trades if self._n_trades else 0.0
        if self._losses > 0:
            avg_win = self._sum_win / self._wins if self._wins else 0.0
            avg_loss = self._sum_loss / self._losses
            self._payoff_ratio = avg_win / avg_loss if avg_loss > 0 else math.inf
        else:
            # 손실 없음 → 사실상 무한 손익비; Kelly compute가 이 케이스를 처리한다.
            self._payoff_ratio = math.inf if self._wins > 0 else 0.0

    def _update_mdd(self, ctx: StrategyContext) -> None:
        """peak equity 추적 + 현재 MDD(%) 갱신.

        equity = ``balance + unrealized_pnl`` (Protocol 표준). 백테스트와
        라이브 모두 동일하게 이 속성을 노출한다.
        """
        try:
            balance = float(getattr(ctx, "balance", 0.0))
            upnl = float(getattr(ctx, "unrealized_pnl", 0.0))
        except Exception:  # noqa: BLE001
            return
        equity = balance + upnl
        if not math.isfinite(equity) or equity <= 0:
            return
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
            self._current_mdd_pct = 0.0
            return
        self._current_mdd_pct = (1.0 - equity / self._peak_equity) * 100.0

    def _emit_event(self, ctx: Any, action: str, data: dict[str, Any]) -> None:
        """ctx.log_event 가 있으면 호출, 없으면 무시."""
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass


def _sign(x: float) -> int:
    """부호 함수. NaN / 0 → 0."""
    if not math.isfinite(x) or x == 0.0:
        return 0
    return 1 if x > 0 else -1
