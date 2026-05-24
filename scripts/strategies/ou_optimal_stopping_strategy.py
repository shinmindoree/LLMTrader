"""Ornstein-Uhlenbeck (OU) mean-reversion + optimal-stopping strategy.

PURPOSE
-------
Single-asset mean-reversion strategy that:

1. Fits an OU process to the recent closed-bar history
       dX_t = mu * (theta - X_t) * dt + sigma * dB_t
   via an OLS AR(1) regression and the standard discrete <-> continuous
   conversion (see ``_fit_ou`` below).

2. Computes the **half-life** of mean reversion
       tau_half = ln(2) / mu
   and refuses to trade when half-life is shorter than ``min_half_life_bars``
   (microstructure noise) or longer than ``max_half_life_bars`` (too slow for
   the chosen timeframe).

3. Uses an **optimal-stopping-inspired** entry/exit policy on the
   z-score
       z = (x - theta) / sigma_inf,   sigma_inf = sigma / sqrt(2 * mu)
   - Entry only inside the band ``[entry_z_lo, entry_z_hi]`` (in the
     direction that reverts to theta). This is the discrete analogue of
     the optimal entry interval ``[a*_L, d*_L]`` from the
     Leung & Li (2015) formulation: don't chase price that has already
     moved too far (above ``d*_L``) and don't enter on small noise (below
     ``a*_L``).
   - Exit at the optimal target ``b*_L`` approximated by ``|z| <= exit_z``.
   - Hard stop-loss at ``|z| >= stop_z`` (the loss barrier ``L``).
   - Time exit at ``max_hold_bars``.

4. Adds a **cost-aware** filter: the expected reversion in basis points
   must clear ``fee_round_trip_bps + min_edge_bps`` before any entry
   fires. This keeps the strategy from churning when the candidate trade
   does not have enough headroom to absorb fees.

The strategy is intentionally lightweight (no parquet seed, no Redis
providers): it relies entirely on ``ctx._get_builtin_indicator_inputs()``
which the backtest engine (500 bars) and the live engine (1000 bars)
both maintain natively. Position sizing is delegated to the runner
trade-settings via ``ctx.enter_long`` / ``ctx.enter_short``.

References
----------
Leung, T., & Li, X. (2015). "Optimal Mean Reversion Trading with
Transaction Costs and Stop-Loss Exit." International Journal of
Theoretical and Applied Finance. The closed-form Kummer-function
barriers are well-approximated by z-score thresholds in the regime
we trade (high-frequency BTC perp), so we expose the thresholds as
tunable params and validate them with a grid sweep instead of solving
the ODE per-bar.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext

# Optimal-stopping barrier solver (closed-form via OU scale function).
# Imported lazily inside the method to avoid a hard dependency for users
# who only ever run the "none" mode.
try:
    from . import _ou_barriers  # type: ignore[attr-defined]
except ImportError:
    _ou_barriers = None  # type: ignore[assignment]
    try:
        # Fallback: same-directory import when this file is loaded via
        # importlib (the backtest engine + scripts/run_backtest do this).
        import importlib.util as _ilu  # noqa: PLC0415
        import os as _os  # noqa: PLC0415
        _here = _os.path.dirname(_os.path.abspath(__file__))
        _spec = _ilu.spec_from_file_location(
            "_ou_barriers", _os.path.join(_here, "_ou_barriers.py")
        )
        if _spec and _spec.loader:
            _ou_barriers = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_ou_barriers)
    except Exception:  # noqa: BLE001
        _ou_barriers = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OU fit helpers
# ---------------------------------------------------------------------------
def _fit_ou(prices_window: list[float], dt: float) -> dict[str, float] | None:
    """OLS AR(1) -> OU parameters.

    Inputs
    ------
    prices_window : list[float]
        Series to fit (typically log-prices). Must have >= 30 samples and
        no NaN/Inf entries.
    dt : float
        Discrete time step in OU "time units". When we pass one bar at a
        time, ``dt = 1.0`` and ``mu`` / ``half_life`` come out in units of
        bars, which is the natural unit for the strategy.

    Returns
    -------
    dict with keys (mu, theta, sigma, sigma_inf, half_life, b, a, s)
    or ``None`` when the fit is degenerate (insufficient samples, zero
    variance, AR(1) coefficient outside (0, 1), or numerical issues).
    """
    n = len(prices_window)
    if n < 30:
        return None
    # Guard against NaN/Inf: a single bad sample destroys the regression.
    for v in prices_window:
        if not math.isfinite(v):
            return None

    # OLS on (x_{t}, x_{t+1})
    n_pairs = n - 1
    x_mean = 0.0
    y_mean = 0.0
    for i in range(n_pairs):
        x_mean += prices_window[i]
        y_mean += prices_window[i + 1]
    x_mean /= n_pairs
    y_mean /= n_pairs

    sxx = 0.0
    sxy = 0.0
    for i in range(n_pairs):
        dx = prices_window[i] - x_mean
        dy = prices_window[i + 1] - y_mean
        sxx += dx * dx
        sxy += dx * dy
    if sxx <= 0.0:
        return None

    b = sxy / sxx
    a = y_mean - b * x_mean
    if not (0.0 < b < 1.0):
        return None

    # Residual std-dev
    sse = 0.0
    for i in range(n_pairs):
        resid = prices_window[i + 1] - (a + b * prices_window[i])
        sse += resid * resid
    dof = max(n_pairs - 2, 1)
    s = math.sqrt(sse / dof)

    try:
        mu = -math.log(b) / dt
        theta = a / (1.0 - b)
        sigma = s * math.sqrt(-2.0 * math.log(b) / ((1.0 - b * b) * dt))
        sigma_inf = sigma / math.sqrt(2.0 * mu)
    except (ValueError, ZeroDivisionError):
        return None
    if not (math.isfinite(mu) and math.isfinite(theta)
            and math.isfinite(sigma) and math.isfinite(sigma_inf)):
        return None
    if sigma_inf <= 0.0 or mu <= 0.0:
        return None

    half_life = math.log(2.0) / mu
    return {
        "mu": mu,
        "theta": theta,
        "sigma": sigma,
        "sigma_inf": sigma_inf,
        "half_life": half_life,
        "b": b,
        "a": a,
        "s": s,
    }


def _bar_ts(bar: dict[str, Any]) -> int:
    """Pull a deterministic timestamp out of the bar dict.

    Mirrors the helper used elsewhere in the strategies package: prefer
    ``bar_timestamp`` (open time of the closed bar) when present, fall
    back to ``timestamp``, default to 0.
    """
    for k in ("bar_timestamp", "timestamp", "ts"):
        v = bar.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return 0


# ---------------------------------------------------------------------------
# Strategy params
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    # --- OU fit window ----------------------------------------------------
    # Number of closed bars used for the OLS AR(1) regression. 240 ~= 60h
    # on 15m bars: long enough for sigma_inf to converge, short enough to
    # track regime changes. Backtest buffers 500 bars / live buffers 1000.
    "ou_window": 240,
    "min_bars_to_trade": 240,
    # Fit log(price) instead of price. Far more stable for crypto where
    # the price level moves an order of magnitude across the dataset.
    "use_log_price": 1,
    # Refit cadence: re-run the OLS every N new bars. 1 = every bar.
    "refit_every_bars": 1,

    # --- Stationarity / half-life filter ---------------------------------
    # Reject fits where the AR(1) coefficient b is outside this band. b
    # close to 1 = unit root (non-mean-reverting); b <= 0 = anti-correlated
    # noise.
    "min_b": 0.05,
    "max_b": 0.99,
    # Half-life filter (in bars). 4 = 1h on 15m bars (any faster = noise),
    # 96 = 24h on 15m bars (any slower = we won't see reversion within
    # ``max_hold_bars``).
    "min_half_life_bars": 4.0,
    "max_half_life_bars": 96.0,

    # --- Cost / discount --------------------------------------------------
    # Round-trip taker fee + slippage estimate. 8 bps = 0.04% taker x 2.
    "fee_round_trip_bps": 8.0,
    # Discount rate (per bar). Kept at 0 for short horizons; the
    # cost-aware filter dominates.
    "discount_r": 0.0,
    # Minimum bps of expected mean-reversion in excess of fees required
    # before any entry fires. Tunable; 4 bps is a conservative default.
    "min_edge_bps": 4.0,

    # --- Optimal stopping barriers (z-space) -----------------------------
    # Entry zone [entry_z_lo, entry_z_hi] (absolute value of z). Discrete
    # analogue of [a*_L, d*_L]. Asymmetric tuning is possible via the
    # *_long / *_short overrides; the defaults are symmetric.
    "entry_z_lo": 1.0,
    "entry_z_hi": 2.5,
    # Exit (target) barrier b*_L: close when |z| drops below this.
    "exit_z": 0.2,
    # Stop-loss barrier L: close when |z| blows past this.
    "stop_z": 3.5,
    # Per-side overrides (use NaN to fall back to the symmetric values).
    # Crypto's funding-driven asymmetry sometimes makes one side worth
    # tightening. NaN = "use symmetric value".
    "entry_z_lo_long": float("nan"),
    "entry_z_hi_long": float("nan"),
    "exit_z_long": float("nan"),
    "stop_z_long": float("nan"),
    "entry_z_lo_short": float("nan"),
    "entry_z_hi_short": float("nan"),
    "exit_z_short": float("nan"),
    "stop_z_short": float("nan"),

    # --- Hold / cooldown --------------------------------------------------
    "max_hold_bars": 48,
    "cooldown_bars": 4,

    # --- Bar gating -------------------------------------------------------
    # If True, entry signals only fire on closed bars (mirrors the bulk of
    # the rest of the strategy package). Tick-mode exits still run on
    # every on_bar call so SL/TP latency stays low.
    "new_bar_only": 1,

    # --- Diagnostics ------------------------------------------------------
    # Emit one OU_FIT event per bar (per refit) with the current fit
    # snapshot. Useful for sanity-checking theta/half-life drift in live
    # mode. Off by default to keep audit volume low.
    "emit_fit_events": 0,

    # --- Closed-form / numerical barrier solver --------------------------
    # Optional: replace the user-supplied entry interval [entry_z_lo,
    # entry_z_hi] (and the exit barrier b) with the **optimal** values
    # derived from the OU first-passage-probability scale function +
    # cost-aware expected PnL. See ``_ou_barriers.py``.
    # Modes:
    #   "none"   : use the user-supplied z thresholds (default; same as
    #              the v1 behaviour).
    #   "solver" : on each refit, call solve_barriers() and override
    #              entry_z_lo/entry_z_hi/exit_z with the solver's output.
    #              stop_z is treated as the user-fixed loss barrier L.
    "barrier_mode": "none",
    # When ``barrier_mode == "solver"``, the solver's a*_L is in z-space;
    # use this to add a safety margin (in z units) to a*_L so we don't
    # enter exactly at the zero-PnL edge.
    "solver_entry_margin": 0.05,
}


STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "ou_window": {
        "type": "integer", "min": 60, "max": 500,
        "label": "OU 추정 윈도우 (봉)",
        "description": "OLS AR(1) 회귀에 사용하는 lookback 봉 수. backtest=500봉/live=1000봉 버퍼 한도 내.",
        "group": "OU 적합",
    },
    "min_bars_to_trade": {
        "type": "integer", "min": 30, "max": 1000,
        "label": "거래 시작 최소 봉 수",
        "description": "이 봉 수가 누적될 때까지는 어떠한 신호도 발생시키지 않음.",
        "group": "OU 적합",
    },
    "use_log_price": {
        "type": "integer", "min": 0, "max": 1,
        "label": "로그 가격 사용 (0/1)",
        "description": "강추: 1. BTC처럼 가격 레벨이 크게 변하는 자산에서 fit 안정성 ↑.",
        "group": "OU 적합",
    },
    "refit_every_bars": {
        "type": "integer", "min": 1, "max": 96,
        "label": "재적합 주기 (봉)",
        "description": "N봉마다 OU 파라미터 재추정. 1=매 봉.",
        "group": "OU 적합",
    },
    "min_b": {
        "type": "number", "min": 0.0, "max": 0.99,
        "label": "AR(1) 계수 하한",
        "description": "b <= min_b이면 fit 거부 (anti-correlated 노이즈 회피).",
        "group": "필터",
    },
    "max_b": {
        "type": "number", "min": 0.01, "max": 1.0,
        "label": "AR(1) 계수 상한",
        "description": "b >= max_b이면 fit 거부 (단위근/비정상성 회피).",
        "group": "필터",
    },
    "min_half_life_bars": {
        "type": "number", "min": 1.0, "max": 100.0,
        "label": "최소 반감기 (봉)",
        "description": "이보다 짧으면 microstructure 노이즈로 간주, 진입 보류.",
        "group": "필터",
    },
    "max_half_life_bars": {
        "type": "number", "min": 5.0, "max": 500.0,
        "label": "최대 반감기 (봉)",
        "description": "이보다 길면 보유시간 안에 회귀가 안 일어남, 진입 보류.",
        "group": "필터",
    },
    "fee_round_trip_bps": {
        "type": "number", "min": 0.0, "max": 50.0,
        "label": "왕복 수수료 (bps)",
        "description": "진입+청산 수수료 + 슬리피지 추정. 8bps ≈ 0.04% × 2 (Binance taker).",
        "group": "비용",
    },
    "min_edge_bps": {
        "type": "number", "min": 0.0, "max": 200.0,
        "label": "최소 엣지 (bps)",
        "description": "예상 회귀폭이 fee + min_edge_bps 미만이면 진입 보류.",
        "group": "비용",
    },
    "entry_z_lo": {
        "type": "number", "min": 0.0, "max": 5.0,
        "label": "진입 구역 하한 (|z|)",
        "description": "|z| ≥ 이 값일 때만 진입 시작 (a*_L barrier).",
        "group": "임계점",
    },
    "entry_z_hi": {
        "type": "number", "min": 0.0, "max": 6.0,
        "label": "진입 구역 상한 (|z|)",
        "description": "|z| > 이 값이면 추격 진입 금지 (d*_L barrier).",
        "group": "임계점",
    },
    "exit_z": {
        "type": "number", "min": 0.0, "max": 3.0,
        "label": "청산 임계 (|z|)",
        "description": "|z| ≤ 이 값에 도달하면 청산 (b*_L barrier).",
        "group": "임계점",
    },
    "stop_z": {
        "type": "number", "min": 1.0, "max": 10.0,
        "label": "손절 임계 (|z|)",
        "description": "|z| ≥ 이 값에 도달하면 손절 (L barrier).",
        "group": "임계점",
    },
    "max_hold_bars": {
        "type": "integer", "min": 1, "max": 1000,
        "label": "최대 보유 봉 수",
        "description": "초과 시 강제 청산. max_half_life_bars의 ~2배가 합리적.",
        "group": "운용",
    },
    "cooldown_bars": {
        "type": "integer", "min": 0, "max": 200,
        "label": "쿨다운 봉 수",
        "description": "청산 직후 N봉 동안 신규 진입 금지.",
        "group": "운용",
    },
    "new_bar_only": {
        "type": "integer", "min": 0, "max": 1,
        "label": "새 봉에서만 진입 (0/1)",
        "description": "0이면 틱마다 진입 평가. 1 권장.",
        "group": "운용",
    },
    "emit_fit_events": {
        "type": "integer", "min": 0, "max": 1,
        "label": "OU_FIT 이벤트 발생 (0/1)",
        "description": "1이면 매 재적합마다 OU_FIT 감사 이벤트를 emit (live 진단용).",
        "group": "진단",
    },
    "barrier_mode": {
        "type": "string",
        "label": "배리어 모드 (none|solver)",
        "description": "solver = OU 척도함수 기반 최적정지 솔버로 entry/exit barrier를 매 refit마다 갱신. none = 사용자 설정값 사용.",
        "group": "임계점",
    },
    "solver_entry_margin": {
        "type": "number", "min": 0.0, "max": 1.0,
        "label": "솔버 진입 마진 (z)",
        "description": "barrier_mode=solver일 때 a*_L에서 안쪽으로 추가하는 z 마진.",
        "group": "임계점",
    },
}


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class OuOptimalStoppingStrategy(Strategy):
    """OU 평균회귀 + cost-aware 최적정지 (단일자산).

    The strategy maintains *no* persistent state across restarts other
    than what the engine provides natively (open position, cooldown
    counter). OU parameters are recomputed from the engine's rolling
    OHLCV buffer, so a restart simply refits on the next bar.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}

        # --- OU fit
        self.ou_window = int(p["ou_window"])
        self.min_bars_to_trade = int(p["min_bars_to_trade"])
        self.use_log_price = bool(int(p["use_log_price"]))
        self.refit_every_bars = max(1, int(p["refit_every_bars"]))

        # --- Filters
        self.min_b = float(p["min_b"])
        self.max_b = float(p["max_b"])
        self.min_half_life_bars = float(p["min_half_life_bars"])
        self.max_half_life_bars = float(p["max_half_life_bars"])

        # --- Cost
        self.fee_round_trip_bps = float(p["fee_round_trip_bps"])
        self.discount_r = float(p["discount_r"])
        self.min_edge_bps = float(p["min_edge_bps"])

        # --- Barriers (symmetric defaults)
        self.entry_z_lo = float(p["entry_z_lo"])
        self.entry_z_hi = float(p["entry_z_hi"])
        self.exit_z = float(p["exit_z"])
        self.stop_z = float(p["stop_z"])

        # Per-side overrides; NaN => fall back to symmetric value.
        def _pick(side_val: float, default: float) -> float:
            return default if (side_val is None or math.isnan(float(side_val))) else float(side_val)

        self.entry_z_lo_long = _pick(p["entry_z_lo_long"], self.entry_z_lo)
        self.entry_z_hi_long = _pick(p["entry_z_hi_long"], self.entry_z_hi)
        self.exit_z_long = _pick(p["exit_z_long"], self.exit_z)
        self.stop_z_long = _pick(p["stop_z_long"], self.stop_z)
        self.entry_z_lo_short = _pick(p["entry_z_lo_short"], self.entry_z_lo)
        self.entry_z_hi_short = _pick(p["entry_z_hi_short"], self.entry_z_hi)
        self.exit_z_short = _pick(p["exit_z_short"], self.exit_z)
        self.stop_z_short = _pick(p["stop_z_short"], self.stop_z)

        # Validate thresholds.
        if not (self.entry_z_lo <= self.entry_z_hi):
            raise ValueError("entry_z_lo must be <= entry_z_hi")
        if not (self.exit_z < self.entry_z_lo):
            raise ValueError("exit_z must be < entry_z_lo (otherwise we exit immediately on entry)")
        if not (self.stop_z > self.entry_z_hi):
            raise ValueError("stop_z must be > entry_z_hi (otherwise entries get stopped out instantly)")

        # --- Hold / cooldown
        self.max_hold_bars = int(p["max_hold_bars"])
        self.cooldown_bars = int(p["cooldown_bars"])

        # --- Bar gating + diagnostics
        self.new_bar_only = bool(int(p["new_bar_only"]))
        self.emit_fit_events = bool(int(p["emit_fit_events"]))

        # --- Barrier solver mode
        self.barrier_mode = str(p["barrier_mode"]).strip().lower()
        if self.barrier_mode not in ("none", "solver"):
            raise ValueError(f"barrier_mode must be 'none' or 'solver', got {self.barrier_mode!r}")
        self.solver_entry_margin = float(p["solver_entry_margin"])

        # --- Runtime state ---------------------------------------------------
        self._mode: str | None = None
        self._last_bar_ts: int = 0
        self._last_fit_bar_ts: int = 0
        # Most recent successful fit; None when filtered out / not yet fit.
        self._fit: dict[str, float] | None = None
        # Most recent solver output (None when barrier_mode == "none" or
        # the solver hasn't run yet).
        self._barriers: Any | None = None
        # Trade lifecycle counters.
        self._bars_in_position: int = 0
        self._bars_since_close: int | None = None
        # Closing latch — same idiom as the rest of the strategies package.
        # Set to True between a close_position call and the next bar where
        # position_size returns to 0, to prevent double-firing exits while
        # the close order is in flight.
        self._is_closing: bool = False
        # Cache the side at entry so we can pick the right per-side barriers
        # after the position is open.
        self._entry_side: int = 0

        # Preserve raw params for web UI.
        self.params = dict(p)
        self.indicator_config: dict[str, Any] = {}

    # ---- lifecycle ---------------------------------------------------------
    def initialize(self, ctx: StrategyContext) -> None:
        ctx_cls = type(ctx).__name__
        ctx_module = type(ctx).__module__
        if "Backtest" in ctx_cls:
            self._mode = "backtest"
        elif (
            "Live" in ctx_cls
            or ctx_cls == "StreamBoundStrategyContext"
            or ctx_module.startswith("live.")
        ):
            self._mode = "live"
        else:
            self._mode = None

        self._last_bar_ts = 0
        self._last_fit_bar_ts = 0
        self._fit = None
        self._bars_in_position = 0
        self._bars_since_close = None
        self._is_closing = False
        self._entry_side = 0

        self._emit_event(ctx, "OU_INIT", {
            "mode": self._mode,
            "ou_window": self.ou_window,
            "use_log_price": int(self.use_log_price),
            "entry_z_lo": self.entry_z_lo,
            "entry_z_hi": self.entry_z_hi,
            "exit_z": self.exit_z,
            "stop_z": self.stop_z,
            "min_half_life_bars": self.min_half_life_bars,
            "max_half_life_bars": self.max_half_life_bars,
            "fee_round_trip_bps": self.fee_round_trip_bps,
            "min_edge_bps": self.min_edge_bps,
            "max_hold_bars": self.max_hold_bars,
            "cooldown_bars": self.cooldown_bars,
        })

    # ---- helpers -----------------------------------------------------------
    def _refit_if_needed(self, ctx: StrategyContext, ts: int) -> dict[str, float] | None:
        """Refit OU parameters from the engine's rolling buffer.

        Returns the new fit (or the cached previous fit when ``refit_every_bars``
        gating skipped a refit this bar). Returns ``None`` when the fit
        was attempted but failed (insufficient data, non-stationary, etc.).
        """
        # Cadence gate.
        if self._fit is not None and self._last_fit_bar_ts > 0:
            bars_since_last_fit = max(0, (ts - self._last_fit_bar_ts) // self._bar_step_ms_safe(ts))
            if bars_since_last_fit < self.refit_every_bars:
                return self._fit

        inputs_fn = getattr(ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs_fn):
            # Engine doesn't expose the buffer (shouldn't happen for the
            # backtest/live contexts shipped with the runner).
            return None
        try:
            inputs = inputs_fn()
        except Exception:  # noqa: BLE001
            return None
        closes = inputs.get("close") if isinstance(inputs, dict) else None
        if not closes:
            return None
        n = len(closes)
        if n < self.min_bars_to_trade:
            return None

        # Slice the trailing ou_window samples.
        window = list(closes[-self.ou_window:])
        if self.use_log_price:
            window = [math.log(v) for v in window if v > 0]
            if len(window) < self.min_bars_to_trade:
                return None

        fit = _fit_ou(window, dt=1.0)
        if fit is None:
            self._fit = None
            self._last_fit_bar_ts = ts
            return None

        # Stationarity / half-life filter.
        if not (self.min_b <= fit["b"] <= self.max_b):
            self._fit = None
            self._last_fit_bar_ts = ts
            return None
        if not (self.min_half_life_bars <= fit["half_life"] <= self.max_half_life_bars):
            self._fit = None
            self._last_fit_bar_ts = ts
            return None

        self._fit = fit
        self._last_fit_bar_ts = ts
        if self.emit_fit_events:
            self._emit_event(ctx, "OU_FIT", {
                "ts": ts,
                "b": fit["b"],
                "theta": fit["theta"],
                "sigma": fit["sigma"],
                "sigma_inf": fit["sigma_inf"],
                "half_life_bars": fit["half_life"],
                "log_price": int(self.use_log_price),
            })

        # Solver hook: when barrier_mode == "solver" and the solver
        # module is available, override the symmetric entry/exit
        # thresholds with the optimal-stopping output. stop_z is treated
        # as the user-fixed loss barrier L; the per-side overrides (long /
        # short) are left untouched so an asymmetric tuning still wins
        # over the symmetric solver values for that side.
        if self.barrier_mode == "solver" and _ou_barriers is not None:
            try:
                barriers = _ou_barriers.solve_barriers(
                    sigma_inf=float(fit["sigma_inf"]),
                    exit_z=self.exit_z,
                    stop_z=self.stop_z,
                    fees_bps=self.fee_round_trip_bps + self.min_edge_bps,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("OU solver failed: %s; falling back to user thresholds", exc)
                barriers = None
            self._barriers = barriers
            if (
                barriers is not None
                and math.isfinite(barriers.a_star)
                and math.isfinite(barriers.d_star)
                and barriers.d_star > barriers.a_star
            ):
                # In z-space, the long entry region is (-stop_z, -exit_z).
                # The solver returns a_star (closer to -stop_z) and d_star
                # (closer to -exit_z). We use them in absolute-value form
                # for the strategy's existing entry-zone check.
                # |z| ∈ [|d_star|, |a_star|]  (since a_star < d_star < 0).
                lo_abs = abs(barriers.d_star) + self.solver_entry_margin
                hi_abs = abs(barriers.a_star) - self.solver_entry_margin
                if lo_abs < hi_abs:
                    self.entry_z_lo_long = lo_abs
                    self.entry_z_hi_long = hi_abs
                    self.entry_z_lo_short = lo_abs
                    self.entry_z_hi_short = hi_abs
                self.exit_z_long = max(0.01, barriers.b_star)
                self.exit_z_short = max(0.01, barriers.b_star)
                if self.emit_fit_events:
                    self._emit_event(ctx, "OU_SOLVER", {
                        "ts": ts,
                        "a_star": barriers.a_star,
                        "d_star": barriers.d_star,
                        "b_star": barriers.b_star,
                        "L_star": barriers.L_star,
                        "expected_pnl_bps": barriers.expected_pnl_at_entry_bps,
                    })

        return fit

    def _bar_step_ms_safe(self, ts: int) -> int:
        """Best-effort bar step in ms; falls back to 1 to avoid div-by-zero.

        We don't have a clean signal of the bar interval from inside the
        strategy, so we infer it from successive ``ts`` values. When the
        first call has no previous ts, return 1 so the cadence gate doesn't
        accidentally suppress the very first fit.
        """
        if self._last_bar_ts <= 0 or ts <= self._last_bar_ts:
            return 1
        return ts - self._last_bar_ts

    def _z_for(self, price: float, fit: dict[str, float]) -> float:
        """Z-score of ``price`` under the OU fit."""
        x = math.log(price) if self.use_log_price else price
        sigma_inf = fit["sigma_inf"]
        if sigma_inf <= 0.0:
            return 0.0
        return (x - fit["theta"]) / sigma_inf

    def _expected_revert_bps(self, z: float, price: float, fit: dict[str, float]) -> float:
        """Expected price move if z reverts to 0 (theta), expressed in bps."""
        if price <= 0.0 or not math.isfinite(z):
            return 0.0
        sigma_inf = fit["sigma_inf"]
        if self.use_log_price:
            # Revert from current log-price to theta == change of -z * sigma_inf
            # in log-space, which is approximately (-z * sigma_inf) in return-space
            # for small moves.
            return abs(z) * sigma_inf * 1e4
        # Linear-price OU: distance to theta is |z| * sigma_inf, in price units.
        return abs(z) * sigma_inf / price * 1e4

    # ---- main loop ---------------------------------------------------------
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # When we're flat, reset trade-lifetime counters so we treat the
        # next entry as fresh. Matches the pattern in
        # bb_rsi_mean_reversion_strategy.py / rsi_long_short_strategy.py.
        if ctx.position_size == 0:
            if self._is_closing:
                self._bars_since_close = 0
            self._is_closing = False
            self._bars_in_position = 0
            self._entry_side = 0

        # Don't fire while orders are still in flight (live mode safety).
        if ctx.get_open_orders():
            return

        price = float(ctx.current_price)
        if price <= 0.0:
            return

        # -------- Exit evaluation (every tick) -----------------------------
        # SL / TP triggers should not wait for bar close — the OU
        # framework relies on hitting the optimal barriers ASAP so PnL
        # tracks the analytical expectation.
        if (
            ctx.position_size != 0
            and not self._is_closing
            and self._fit is not None
        ):
            side = 1 if ctx.position_size > 0 else -1
            z = self._z_for(price, self._fit)
            # Pick per-side barriers.
            exit_z = self.exit_z_long if side > 0 else self.exit_z_short
            stop_z = self.stop_z_long if side > 0 else self.stop_z_short

            # Stop-loss: long is stopped if z drops further below theta
            # (more negative); short is stopped if z climbs further above
            # theta (more positive).
            if side > 0 and z <= -stop_z:
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU SL long z={z:.2f} <= -{stop_z:.2f}",
                    exit_reason="STOP_LOSS",
                )
                self._emit_event(ctx, "OU_STOP", {
                    "side": int(side), "z": z, "stop_z": float(stop_z),
                    "fit": self._fit_snapshot(),
                })
                return
            if side < 0 and z >= stop_z:
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU SL short z={z:.2f} >= {stop_z:.2f}",
                    exit_reason="STOP_LOSS",
                )
                self._emit_event(ctx, "OU_STOP", {
                    "side": int(side), "z": z, "stop_z": float(stop_z),
                    "fit": self._fit_snapshot(),
                })
                return

            # Take-profit at b*_L (z back to ~0).
            if abs(z) <= exit_z:
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU TP z={z:.2f} (|z| <= {exit_z:.2f})",
                    exit_reason="TAKE_PROFIT",
                )
                self._emit_event(ctx, "OU_TARGET", {
                    "side": int(side), "z": z, "exit_z": float(exit_z),
                    "fit": self._fit_snapshot(),
                })
                return

        # -------- Bar-close work (refit + entries + time exit) -------------
        if self.new_bar_only and not bool(bar.get("is_new_bar", True)):
            return

        ts = _bar_ts(bar)
        if ts <= 0 or ts == self._last_bar_ts:
            return

        # Cooldown counter ticks on every new closed bar.
        if self._bars_since_close is not None:
            self._bars_since_close += 1

        # Refit OU parameters (or reuse cached fit per cadence).
        fit = self._refit_if_needed(ctx, ts)
        self._last_bar_ts = ts

        # If we're in a position, handle time exit and skip entries.
        if ctx.position_size != 0:
            self._bars_in_position += 1
            if (
                self.max_hold_bars > 0
                and self._bars_in_position >= self.max_hold_bars
                and not self._is_closing
            ):
                self._is_closing = True
                self._bars_since_close = 0
                ctx.close_position(
                    reason=f"OU time exit after {self._bars_in_position} bars",
                    exit_reason="TIME_EXIT",
                )
                self._emit_event(ctx, "OU_TIME_EXIT", {
                    "bars_held": int(self._bars_in_position),
                    "side": int(self._entry_side),
                    "fit": self._fit_snapshot(),
                })
            return

        # ----- Entry evaluation ---------------------------------------------
        if fit is None:
            return
        # Cooldown gate.
        if (
            self._bars_since_close is not None
            and self._bars_since_close < self.cooldown_bars
        ):
            return

        z = self._z_for(price, fit)
        edge_bps = self._expected_revert_bps(z, price, fit)
        # Cost guard: skip when the analytical edge can't clear costs.
        if edge_bps < (self.fee_round_trip_bps + self.min_edge_bps):
            return

        # Long when price is below theta (negative z); short when above.
        # The entry zone is checked in absolute-value space.
        abs_z = abs(z)
        if z < 0.0:
            lo = self.entry_z_lo_long
            hi = self.entry_z_hi_long
        else:
            lo = self.entry_z_lo_short
            hi = self.entry_z_hi_short

        if not (lo <= abs_z <= hi):
            return

        # Fire entry.
        side = -1 if z > 0.0 else 1
        if side > 0:
            ctx.enter_long(
                reason=f"OU long z={z:.2f} hl={fit['half_life']:.1f} edge={edge_bps:.1f}bps",
            )
        else:
            ctx.enter_short(
                reason=f"OU short z={z:.2f} hl={fit['half_life']:.1f} edge={edge_bps:.1f}bps",
            )
        self._entry_side = side
        self._bars_in_position = 0
        self._bars_since_close = None
        self._emit_event(ctx, "OU_ENTER", {
            "side": int(side),
            "z": z,
            "edge_bps": edge_bps,
            "fee_bps": self.fee_round_trip_bps,
            "fit": self._fit_snapshot(),
        })

    # ---- utility -----------------------------------------------------------
    def _fit_snapshot(self) -> dict[str, float] | None:
        """JSON-friendly subset of the current fit for audit events."""
        if self._fit is None:
            return None
        return {
            "b": float(self._fit["b"]),
            "theta": float(self._fit["theta"]),
            "sigma_inf": float(self._fit["sigma_inf"]),
            "half_life_bars": float(self._fit["half_life"]),
        }

    @staticmethod
    def _emit_event(ctx: Any, action: str, data: dict[str, Any]) -> None:
        """Best-effort audit-event emission. Mirrors MFP convention."""
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass
