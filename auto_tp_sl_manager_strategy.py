"""Auto TP & SL Manager (ML regime classification + kernel validation + ATR TP/SL) 전략.

목표:
- KNN(유사도 기반)로 시장 레짐/방향성을 분류하고,
- 커널 회귀(Rational Quadratic + Gaussian)로 추세를 검증한 뒤,
- ATR 기반 TP/SL로 포지션을 자동 관리한다.

주의:
- 본 구현은 시스템 프레임워크(StrategyContext) 제약 내에서 "바-클로즈 신호 + 옵션 틱 TP/SL"로 동작한다.
- 신호 계산은 `is_new_bar=True`(닫힌 봉 기준)에서만 수행하여 non-repainting을 보장한다.
- TP/SL 관리는 `run_on_tick=True` 설정 시 tick에서도 수행 가능(라이브 엔진이 tick 콜백을 전달).

포맷:
- `indicator_strategy_template.py`의 구조(guard → 지표조회 → 신호판단 → 상태업데이트)를 따른다.

실행 예시:
- 라이브: `uv run python scripts/run_live_trading.py auto_tp_sl_manager_strategy.py --strategy-params '{"knn_k":8}'`
- 백테스트: `uv run python scripts/run_backtest.py auto_tp_sl_manager_strategy.py --start-date 2024-01-01 --end-date 2024-06-01`
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Iterable, Sequence

from strategy.base import Strategy
from strategy.context import StrategyContext


def _is_finite(x: float) -> bool:
    return math.isfinite(float(x))


def _sign(x: float, *, eps: float = 0.0) -> int:
    v = float(x)
    if v > eps:
        return 1
    if v < -eps:
        return -1
    return 0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _sma_series(values: Sequence[float], period: int) -> list[float]:
    n = len(values)
    if period <= 0 or n == 0:
        return []
    out = [float("nan")] * n
    if n < period:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += float(v)
        if i >= period:
            s -= float(values[i - period])
        if i >= period - 1:
            out[i] = s / float(period)
    return out


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    n = len(values)
    if period <= 1 or n == 0:
        return [float("nan")] * n
    out = [float("nan")] * n
    if n < period:
        return out
    alpha = 2.0 / (float(period) + 1.0)
    # 첫 `period` 구간이 NaN을 포함할 수 있으므로, "연속된 finite window"를 찾은 뒤 시작한다.
    start = None
    for i in range(0, n - period + 1):
        window = values[i : i + period]
        if all(_is_finite(float(v)) for v in window):
            start = i
            break
    if start is None:
        return out
    ema = fmean([float(v) for v in values[start : start + period]])
    idx0 = start + period - 1
    out[idx0] = float(ema)
    for i in range(idx0 + 1, n):
        v = float(values[i])
        if not _is_finite(v):
            out[i] = float("nan")
            continue
        ema = alpha * v + (1.0 - alpha) * float(ema)
        out[i] = float(ema)
    return out


def _wilder_smooth(values: Sequence[float], period: int) -> list[float]:
    """Wilder smoothing (RMA) series. period-1까지는 NaN."""
    n = len(values)
    if period <= 0 or n == 0:
        return []
    out = [float("nan")] * n
    if n < period:
        return out
    rma = fmean(values[:period])
    out[period - 1] = float(rma)
    for i in range(period, n):
        rma = (float(rma) * float(period - 1) + float(values[i])) / float(period)
        out[i] = float(rma)
    return out


def _rsi_series(closes: Sequence[float], period: int) -> list[float]:
    n = len(closes)
    if period <= 0 or n == 0:
        return []
    out = [float("nan")] * n
    if n <= period:
        return out
    gains: list[float] = [0.0] * n
    losses: list[float] = [0.0] * n
    for i in range(1, n):
        delta = float(closes[i]) - float(closes[i - 1])
        gains[i] = max(0.0, delta)
        losses[i] = max(0.0, -delta)
    avg_gain = fmean(gains[1 : period + 1])
    avg_loss = fmean(losses[1 : period + 1])
    rs = (avg_gain / avg_loss) if avg_loss > 1e-12 else float("inf")
    out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / float(period)
        avg_loss = (avg_loss * (period - 1) + losses[i]) / float(period)
        rs = (avg_gain / avg_loss) if avg_loss > 1e-12 else float("inf")
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _atr_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int) -> list[float]:
    n = min(len(highs), len(lows), len(closes))
    if period <= 0 or n == 0:
        return []
    tr = [float("nan")] * n
    for i in range(n):
        h = float(highs[i])
        l = float(lows[i])
        if i == 0:
            tr[i] = h - l
        else:
            pc = float(closes[i - 1])
            tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    return _wilder_smooth(tr, period)


def _cci_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int) -> list[float]:
    n = min(len(highs), len(lows), len(closes))
    if period <= 0 or n == 0:
        return []
    tp = [(float(highs[i]) + float(lows[i]) + float(closes[i])) / 3.0 for i in range(n)]
    sma = _sma_series(tp, period)
    out = [float("nan")] * n
    if n < period:
        return out
    for i in range(period - 1, n):
        mean_tp = float(sma[i])
        window = tp[i - period + 1 : i + 1]
        dev = fmean([abs(float(x) - mean_tp) for x in window])
        denom = 0.015 * dev
        out[i] = ((float(tp[i]) - mean_tp) / denom) if denom > 1e-12 else 0.0
    return out


def _adx_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int) -> list[float]:
    n = min(len(highs), len(lows), len(closes))
    if period <= 0 or n == 0:
        return []
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = float(highs[i]) - float(highs[i - 1])
        down = float(lows[i - 1]) - float(lows[i])
        plus_dm[i] = up if (up > down and up > 0.0) else 0.0
        minus_dm[i] = down if (down > up and down > 0.0) else 0.0
        h = float(highs[i])
        l = float(lows[i])
        pc = float(closes[i - 1])
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    sm_tr = _wilder_smooth(tr, period)
    sm_plus = _wilder_smooth(plus_dm, period)
    sm_minus = _wilder_smooth(minus_dm, period)

    dx = [float("nan")] * n
    for i in range(n):
        if not (_is_finite(sm_tr[i]) and sm_tr[i] > 1e-12):
            continue
        pdi = 100.0 * float(sm_plus[i]) / float(sm_tr[i])
        mdi = 100.0 * float(sm_minus[i]) / float(sm_tr[i])
        denom = pdi + mdi
        dx[i] = (100.0 * abs(pdi - mdi) / denom) if denom > 1e-12 else 0.0

    return _wilder_smooth(dx, period)


def _wavetrend_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    n1: int,
    n2: int,
) -> list[float]:
    n = min(len(highs), len(lows), len(closes))
    if n == 0 or n1 <= 1 or n2 <= 1:
        return []
    tp = [(float(highs[i]) + float(lows[i]) + float(closes[i])) / 3.0 for i in range(n)]
    esa = _ema_series(tp, n1)
    de_src = [abs(float(tp[i]) - float(esa[i])) if _is_finite(esa[i]) else float("nan") for i in range(n)]
    de = _ema_series(de_src, n1)
    ci = [float("nan")] * n
    for i in range(n):
        if not (_is_finite(esa[i]) and _is_finite(de[i])):
            continue
        denom = 0.015 * float(de[i])
        ci[i] = ((float(tp[i]) - float(esa[i])) / denom) if denom > 1e-12 else 0.0
    return _ema_series(ci, n2)


def _kernel_weights_rq(lookback: int, *, alpha: float, length_scale: float) -> list[float]:
    out: list[float] = []
    a = float(alpha)
    ls = max(1e-6, float(length_scale))
    denom_base = 2.0 * a * (ls * ls)
    for offset in range(lookback):
        r2 = float(offset * offset)
        out.append((1.0 + (r2 / denom_base)) ** (-a))
    return out


def _kernel_weights_gauss(lookback: int, *, length_scale: float) -> list[float]:
    out: list[float] = []
    ls = max(1e-6, float(length_scale))
    denom = 2.0 * (ls * ls)
    for offset in range(lookback):
        out.append(math.exp(-(float(offset * offset) / denom)))
    return out


def _kernel_regression_last(values: Sequence[float], weights: Sequence[float]) -> tuple[float, float]:
    """Return (estimate_at_last, estimate_at_prev)."""
    n = len(values)
    lb = len(weights)
    if n < lb + 1 or lb <= 1:
        return float("nan"), float("nan")
    sw = float(sum(weights))
    if sw <= 1e-12:
        return float("nan"), float("nan")
    est = 0.0
    est_prev = 0.0
    for i, w in enumerate(weights):
        est += float(values[n - 1 - i]) * w
        est_prev += float(values[n - 2 - i]) * w
    return est / sw, est_prev / sw


def _feature_vector(
    *,
    close: float,
    rsi: float,
    wt: float,
    cci: float,
    adx: float,
    ema_long: float,
    atr: float,
    eps: float,
) -> tuple[float, float, float, float, float]:
    # 단위/스케일을 맞춰 distance metric이 특정 feature에 과도하게 치우치지 않도록 정규화한다.
    rsi_n = _clamp((float(rsi) - 50.0) / 50.0, -2.0, 2.0)
    wt_n = _clamp(float(wt) / 100.0, -5.0, 5.0)
    cci_n = _clamp(float(cci) / 200.0, -5.0, 5.0)
    adx_n = _clamp(float(adx) / 50.0, 0.0, 3.0)  # ADX는 강도(0~100)에 가까우므로 양수만
    denom = float(atr) if _is_finite(atr) and float(atr) > eps else 1.0
    price_dev_n = _clamp((float(close) - float(ema_long)) / denom, -5.0, 5.0)
    return (rsi_n, wt_n, cci_n, adx_n, price_dev_n)


def _distance_log_l1(current: Sequence[float], hist: Sequence[float]) -> float:
    d = 0.0
    for a, b in zip(current, hist, strict=False):
        d += math.log1p(abs(float(a) - float(b)))
    return float(d)


def _nan_outputs(keys: Iterable[str]) -> dict[str, float]:
    return {str(k): float("nan") for k in keys}


_INDICATOR_KEYS = (
    "signal",
    "filters_pass",
    "prediction_score",
    "prediction_sign",
    "confidence",
    "rq_est",
    "rq_trend",
    "gauss_est",
    "gauss_trend",
    "atr",
    "atr_pct",
    "tp_distance",
    "sl_distance",
    "long_tp",
    "long_sl",
    "short_tp",
    "short_sl",
)


def _auto_tp_sl_manager_indicator(
    inner_ctx: Any,
    *,
    # KNN (ML) params
    knn_k: int = 8,
    max_lookback: int = 2000,
    label_horizon: int = 10,
    min_confidence: int = 4,
    # Feature params
    rsi_period: int = 14,
    wt_n1: int = 10,
    wt_n2: int = 21,
    cci_period: int = 20,
    adx_period: int = 14,
    ema_long_period: int = 200,
    atr_period: int = 14,
    # Kernel params
    kernel_lookback: int = 200,
    rq_alpha: float = 1.0,
    rq_length_scale: float = 20.0,
    gauss_length_scale: float = 10.0,
    require_gauss_confirm: bool = True,
    require_gauss_crossover: bool = False,
    # Filters
    use_volatility_filter: bool = True,
    min_atr_pct: float = 0.001,
    use_regime_filter: bool = False,
    regime_bars: int = 3,
    use_adx_filter: bool = True,
    min_adx: float = 18.0,
    use_ema_filter: bool = True,
    use_sma_filter: bool = False,
    sma_filter_period: int = 200,
    # Risk (ATR TP/SL)
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 1.5,
    eps: float = 1e-12,
    **_kwargs: Any,
) -> dict[str, float]:
    inputs_fn = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
    if not callable(inputs_fn):
        return _nan_outputs(_INDICATOR_KEYS)

    inputs = inputs_fn()
    if not isinstance(inputs, dict):
        return _nan_outputs(_INDICATOR_KEYS)

    closes_raw = inputs.get("close") or []
    highs_raw = inputs.get("high") or []
    lows_raw = inputs.get("low") or []

    closes = [float(x) for x in closes_raw]
    highs = [float(x) for x in highs_raw]
    lows = [float(x) for x in lows_raw]

    n = min(len(closes), len(highs), len(lows))
    if n <= 10:
        return _nan_outputs(_INDICATOR_KEYS)
    closes = closes[:n]
    highs = highs[:n]
    lows = lows[:n]

    # --- compute feature series (closed bars only) ---
    rsi = _rsi_series(closes, int(rsi_period))
    wt = _wavetrend_series(highs, lows, closes, int(wt_n1), int(wt_n2))
    cci = _cci_series(highs, lows, closes, int(cci_period))
    adx = _adx_series(highs, lows, closes, int(adx_period))
    atr = _atr_series(highs, lows, closes, int(atr_period))
    ema_long = _ema_series(closes, int(ema_long_period))
    sma_long = _sma_series(closes, int(sma_filter_period)) if bool(use_sma_filter) else []

    idx_now = n - 1
    close_now = float(closes[idx_now])
    atr_now = float(atr[idx_now]) if idx_now < len(atr) else float("nan")
    ema_now = float(ema_long[idx_now]) if idx_now < len(ema_long) else float("nan")
    rsi_now = float(rsi[idx_now]) if idx_now < len(rsi) else float("nan")
    wt_now = float(wt[idx_now]) if idx_now < len(wt) else float("nan")
    cci_now = float(cci[idx_now]) if idx_now < len(cci) else float("nan")
    adx_now = float(adx[idx_now]) if idx_now < len(adx) else float("nan")

    if not all(_is_finite(v) for v in (close_now, atr_now, ema_now, rsi_now, wt_now, cci_now, adx_now)):
        return _nan_outputs(_INDICATOR_KEYS)

    # --- kernel regression trend validation ---
    lb = max(10, int(kernel_lookback))
    lb_eff = min(lb, n - 1)
    if lb_eff <= 2:
        return _nan_outputs(_INDICATOR_KEYS)

    rq_w = _kernel_weights_rq(lb_eff, alpha=float(rq_alpha), length_scale=float(rq_length_scale))
    g_w = _kernel_weights_gauss(lb_eff, length_scale=float(gauss_length_scale))
    rq_est, rq_prev = _kernel_regression_last(closes, rq_w)
    g_est, g_prev = _kernel_regression_last(closes, g_w)
    rq_trend = float(_sign(rq_est - rq_prev))
    g_trend = float(_sign(g_est - g_prev))

    # --- ML: KNN regime classification ---
    k = max(1, int(knn_k))
    horizon = max(1, int(label_horizon))
    min_conf = max(1, int(min_confidence))
    lookback = max(50, int(max_lookback))

    # 학습 가능한 마지막 인덱스(미래 라벨 필요)
    idx_train_end = idx_now - horizon
    if idx_train_end <= 0:
        return _nan_outputs(_INDICATOR_KEYS)

    # feature가 준비되는 최소 인덱스(최소 period들 고려)
    idx_feat_min = max(
        int(rsi_period) + 1,
        int(wt_n1) + int(wt_n2),
        int(cci_period),
        int(adx_period) * 2,
        int(atr_period),
        int(ema_long_period),
        lb_eff + 1,
    )
    if idx_train_end < idx_feat_min:
        return _nan_outputs(_INDICATOR_KEYS)

    idx_train_start = max(idx_feat_min, idx_train_end - min(lookback, idx_train_end))

    cur_vec = _feature_vector(
        close=close_now,
        rsi=rsi_now,
        wt=wt_now,
        cci=cci_now,
        adx=adx_now,
        ema_long=ema_now,
        atr=atr_now,
        eps=float(eps),
    )

    distances: list[tuple[float, int]] = []
    for t in range(idx_train_start, idx_train_end + 1):
        rr = float(rsi[t])
        ww = float(wt[t]) if t < len(wt) else float("nan")
        cc = float(cci[t])
        aa = float(adx[t])
        ee = float(ema_long[t])
        at = float(atr[t])
        if not all(_is_finite(v) for v in (rr, ww, cc, aa, ee, at)):
            continue
        hist_vec = _feature_vector(
            close=float(closes[t]),
            rsi=rr,
            wt=ww,
            cci=cc,
            adx=aa,
            ema_long=ee,
            atr=at,
            eps=float(eps),
        )
        dist = _distance_log_l1(cur_vec, hist_vec)
        forward = float(closes[t + horizon]) - float(closes[t])
        label = _sign(forward)
        distances.append((dist, int(label)))

    if len(distances) < k:
        return _nan_outputs(_INDICATOR_KEYS)

    distances.sort(key=lambda x: x[0])
    neighbors = distances[:k]
    prediction_score = float(sum(lbl for _d, lbl in neighbors))
    confidence = float(abs(prediction_score))
    prediction_sign = float(_sign(prediction_score, eps=float(min_conf) - 1e-9))  # abs(score) >= min_conf

    # --- filters (independent, configurable) ---
    filters_pass = True

    atr_pct = (atr_now / close_now) if close_now > 1e-12 else float("nan")
    if bool(use_volatility_filter):
        if not (_is_finite(atr_pct) and atr_pct >= float(min_atr_pct)):
            filters_pass = False

    if bool(use_adx_filter):
        if not (_is_finite(adx_now) and adx_now >= float(min_adx)):
            filters_pass = False

    if bool(use_ema_filter):
        if prediction_sign > 0 and not (close_now >= ema_now):
            filters_pass = False
        if prediction_sign < 0 and not (close_now <= ema_now):
            filters_pass = False

    if bool(use_sma_filter):
        sma_now = float(sma_long[idx_now]) if idx_now < len(sma_long) else float("nan")
        if not _is_finite(sma_now):
            filters_pass = False
        else:
            if prediction_sign > 0 and not (close_now >= sma_now):
                filters_pass = False
            if prediction_sign < 0 and not (close_now <= sma_now):
                filters_pass = False

    # regime persistence: kernel trend가 최근 N개 봉 동안 같은 방향인지 확인
    if bool(use_regime_filter):
        rb = max(2, int(regime_bars))
        if idx_now < (rb + lb_eff + 1):
            filters_pass = False
        else:
            ok = True
            for back in range(rb):
                end = idx_now - back
                sub = closes[: end + 1]
                rq_e, rq_p = _kernel_regression_last(sub, rq_w)
                if float(_sign(rq_e - rq_p)) != float(rq_trend) or float(rq_trend) == 0.0:
                    ok = False
                    break
            if not ok:
                filters_pass = False

    # kernel alignment (statistical validation)
    if prediction_sign == 0.0:
        filters_pass = False
    if float(rq_trend) == 0.0 or float(prediction_sign) != float(rq_trend):
        filters_pass = False
    if bool(require_gauss_confirm):
        if float(g_trend) == 0.0 or float(prediction_sign) != float(g_trend):
            filters_pass = False
    if bool(require_gauss_crossover):
        crossover = float(_sign(rq_est - g_est))
        if crossover == 0.0 or crossover != float(prediction_sign):
            filters_pass = False

    signal = float(prediction_sign) if filters_pass else 0.0

    tp_dist = float(tp_atr_mult) * float(atr_now)
    sl_dist = float(sl_atr_mult) * float(atr_now)

    return {
        "signal": float(signal),
        "filters_pass": 1.0 if filters_pass else 0.0,
        "prediction_score": float(prediction_score),
        "prediction_sign": float(prediction_sign),
        "confidence": float(confidence),
        "rq_est": float(rq_est),
        "rq_trend": float(rq_trend),
        "gauss_est": float(g_est),
        "gauss_trend": float(g_trend),
        "atr": float(atr_now),
        "atr_pct": float(atr_pct),
        "tp_distance": float(tp_dist),
        "sl_distance": float(sl_dist),
        "long_tp": float(close_now + tp_dist),
        "long_sl": float(close_now - sl_dist),
        "short_tp": float(close_now - tp_dist),
        "short_sl": float(close_now + sl_dist),
    }


@dataclass
class _TradeState:
    side: int  # +1 long, -1 short
    entry_price: float
    atr: float
    tp: float
    sl: float
    bars_held: int = 0
    exit_pending: bool = False
    last_exit_reason: str | None = None


class AutoTpSlManagerStrategy(Strategy):
    """Auto TP & SL Manager 전략(롱/숏).

    - ML(KNN) + 커널회귀 검증으로 바-클로즈 시그널 생성
    - ATR 기반 TP/SL을 자동 계산/관리
    """

    INDICATOR_NAME = "AUTO_TP_SL_MANAGER"

    # 라이브에서 tick 콜백을 받아 TP/SL을 더 촘촘히 관리하고 싶으면 True.
    # (신호 계산은 여전히 is_new_bar=True에서만 수행한다.)
    run_on_tick: bool = True

    def __init__(
        self,
        *,
        # KNN
        knn_k: int = 8,
        max_lookback: int = 2000,
        label_horizon: int = 10,
        min_confidence: int = 4,
        # Feature
        rsi_period: int = 14,
        wt_n1: int = 10,
        wt_n2: int = 21,
        cci_period: int = 20,
        adx_period: int = 14,
        ema_long_period: int = 200,
        atr_period: int = 14,
        # Kernel
        kernel_lookback: int = 200,
        rq_alpha: float = 1.0,
        rq_length_scale: float = 20.0,
        gauss_length_scale: float = 10.0,
        require_gauss_confirm: bool = True,
        require_gauss_crossover: bool = False,
        # Filters
        use_volatility_filter: bool = True,
        min_atr_pct: float = 0.001,
        use_regime_filter: bool = False,
        regime_bars: int = 3,
        use_adx_filter: bool = True,
        min_adx: float = 18.0,
        use_ema_filter: bool = True,
        use_sma_filter: bool = False,
        sma_filter_period: int = 200,
        # Risk
        tp_atr_mult: float = 2.0,
        sl_atr_mult: float = 1.5,
        dynamic_exit_on_kernel_reversal: bool = True,
        # Execution
        entry_pct: float | None = None,
    ) -> None:
        super().__init__()

        self.knn_k = int(knn_k)
        self.max_lookback = int(max_lookback)
        self.label_horizon = int(label_horizon)
        self.min_confidence = int(min_confidence)

        self.rsi_period = int(rsi_period)
        self.wt_n1 = int(wt_n1)
        self.wt_n2 = int(wt_n2)
        self.cci_period = int(cci_period)
        self.adx_period = int(adx_period)
        self.ema_long_period = int(ema_long_period)
        self.atr_period = int(atr_period)

        self.kernel_lookback = int(kernel_lookback)
        self.rq_alpha = float(rq_alpha)
        self.rq_length_scale = float(rq_length_scale)
        self.gauss_length_scale = float(gauss_length_scale)
        self.require_gauss_confirm = bool(require_gauss_confirm)
        self.require_gauss_crossover = bool(require_gauss_crossover)

        self.use_volatility_filter = bool(use_volatility_filter)
        self.min_atr_pct = float(min_atr_pct)
        self.use_regime_filter = bool(use_regime_filter)
        self.regime_bars = int(regime_bars)
        self.use_adx_filter = bool(use_adx_filter)
        self.min_adx = float(min_adx)
        self.use_ema_filter = bool(use_ema_filter)
        self.use_sma_filter = bool(use_sma_filter)
        self.sma_filter_period = int(sma_filter_period)

        self.tp_atr_mult = float(tp_atr_mult)
        self.sl_atr_mult = float(sl_atr_mult)
        self.dynamic_exit_on_kernel_reversal = bool(dynamic_exit_on_kernel_reversal)

        self.entry_pct = float(entry_pct) if entry_pct is not None else None

        self.is_closing: bool = False
        self._trade: _TradeState | None = None
        self._trades_total: int = 0
        self._wins: int = 0
        self._losses: int = 0

        self.params = {
            "knn_k": self.knn_k,
            "max_lookback": self.max_lookback,
            "label_horizon": self.label_horizon,
            "min_confidence": self.min_confidence,
            "rsi_period": self.rsi_period,
            "wt_n1": self.wt_n1,
            "wt_n2": self.wt_n2,
            "cci_period": self.cci_period,
            "adx_period": self.adx_period,
            "ema_long_period": self.ema_long_period,
            "atr_period": self.atr_period,
            "kernel_lookback": self.kernel_lookback,
            "rq_alpha": self.rq_alpha,
            "rq_length_scale": self.rq_length_scale,
            "gauss_length_scale": self.gauss_length_scale,
            "require_gauss_confirm": self.require_gauss_confirm,
            "require_gauss_crossover": self.require_gauss_crossover,
            "use_volatility_filter": self.use_volatility_filter,
            "min_atr_pct": self.min_atr_pct,
            "use_regime_filter": self.use_regime_filter,
            "regime_bars": self.regime_bars,
            "use_adx_filter": self.use_adx_filter,
            "min_adx": self.min_adx,
            "use_ema_filter": self.use_ema_filter,
            "use_sma_filter": self.use_sma_filter,
            "sma_filter_period": self.sma_filter_period,
            "tp_atr_mult": self.tp_atr_mult,
            "sl_atr_mult": self.sl_atr_mult,
            "dynamic_exit_on_kernel_reversal": self.dynamic_exit_on_kernel_reversal,
            "entry_pct": self.entry_pct,
        }

        # 로그/스냅샷용 지표 설정
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "knn_k": self.knn_k,
                "max_lookback": self.max_lookback,
                "label_horizon": self.label_horizon,
                "min_confidence": self.min_confidence,
                "rsi_period": self.rsi_period,
                "wt_n1": self.wt_n1,
                "wt_n2": self.wt_n2,
                "cci_period": self.cci_period,
                "adx_period": self.adx_period,
                "ema_long_period": self.ema_long_period,
                "atr_period": self.atr_period,
                "kernel_lookback": self.kernel_lookback,
                "rq_alpha": self.rq_alpha,
                "rq_length_scale": self.rq_length_scale,
                "gauss_length_scale": self.gauss_length_scale,
                "require_gauss_confirm": self.require_gauss_confirm,
                "require_gauss_crossover": self.require_gauss_crossover,
                "use_volatility_filter": self.use_volatility_filter,
                "min_atr_pct": self.min_atr_pct,
                "use_regime_filter": self.use_regime_filter,
                "regime_bars": self.regime_bars,
                "use_adx_filter": self.use_adx_filter,
                "min_adx": self.min_adx,
                "use_ema_filter": self.use_ema_filter,
                "use_sma_filter": self.use_sma_filter,
                "sma_filter_period": self.sma_filter_period,
                "tp_atr_mult": self.tp_atr_mult,
                "sl_atr_mult": self.sl_atr_mult,
            }
        }

    def initialize(self, ctx: StrategyContext) -> None:
        ctx.register_indicator(self.INDICATOR_NAME, _auto_tp_sl_manager_indicator)
        self.is_closing = False
        self._trade = None

    def _maybe_finalize_trade(self, ctx: StrategyContext, price: float) -> None:
        if self._trade is None:
            return
        if abs(ctx.position_size) > 1e-12:
            return
        exit_price = float(price)
        entry_price = float(self._trade.entry_price)
        side = int(self._trade.side)
        pnl = (exit_price - entry_price) * float(side)
        self._trades_total += 1
        if pnl > 0:
            self._wins += 1
        else:
            self._losses += 1
        self._trade = None
        self.is_closing = False

    def _ensure_trade_state(self, ctx: StrategyContext, atr: float) -> None:
        if abs(ctx.position_size) < 1e-12:
            return
        if self._trade is not None:
            return
        side = 1 if ctx.position_size > 0 else -1
        entry_price = float(ctx.position_entry_price or ctx.current_price)
        atr_f = float(atr)
        tp_dist = self.tp_atr_mult * atr_f
        sl_dist = self.sl_atr_mult * atr_f
        if side > 0:
            tp = entry_price + tp_dist
            sl = entry_price - sl_dist
        else:
            tp = entry_price - tp_dist
            sl = entry_price + sl_dist
        self._trade = _TradeState(side=side, entry_price=entry_price, atr=atr_f, tp=tp, sl=sl)

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 미체결 주문 가드(라이브 전용) =====
        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        price = float(bar.get("price", bar.get("close", ctx.current_price)))

        # ===== 포지션이 없으면 청산 플래그 리셋 + trade finalize =====
        if abs(ctx.position_size) < 1e-12:
            self._maybe_finalize_trade(ctx, price)
            self.is_closing = False

        # ===== TP/SL 관리 (tick 포함) =====
        if self._trade is not None and abs(ctx.position_size) > 1e-12 and not self.is_closing:
            t = self._trade
            if t.side > 0:
                if price <= float(t.sl):
                    self.is_closing = True
                    t.exit_pending = True
                    t.last_exit_reason = "SL"
                    ctx.close_position(reason=f"AutoSL Long (price {price:.2f} <= {t.sl:.2f})")
                    return
                if price >= float(t.tp):
                    self.is_closing = True
                    t.exit_pending = True
                    t.last_exit_reason = "TP"
                    ctx.close_position(reason=f"AutoTP Long (price {price:.2f} >= {t.tp:.2f})")
                    return
            else:
                if price >= float(t.sl):
                    self.is_closing = True
                    t.exit_pending = True
                    t.last_exit_reason = "SL"
                    ctx.close_position(reason=f"AutoSL Short (price {price:.2f} >= {t.sl:.2f})")
                    return
                if price <= float(t.tp):
                    self.is_closing = True
                    t.exit_pending = True
                    t.last_exit_reason = "TP"
                    ctx.close_position(reason=f"AutoTP Short (price {price:.2f} <= {t.tp:.2f})")
                    return

        # ===== 신호 계산/진입/청산은 새 봉에서만 수행 =====
        if not bool(bar.get("is_new_bar", True)):
            return

        ind = ctx.get_indicator(
            self.INDICATOR_NAME,
            knn_k=self.knn_k,
            max_lookback=self.max_lookback,
            label_horizon=self.label_horizon,
            min_confidence=self.min_confidence,
            rsi_period=self.rsi_period,
            wt_n1=self.wt_n1,
            wt_n2=self.wt_n2,
            cci_period=self.cci_period,
            adx_period=self.adx_period,
            ema_long_period=self.ema_long_period,
            atr_period=self.atr_period,
            kernel_lookback=self.kernel_lookback,
            rq_alpha=self.rq_alpha,
            rq_length_scale=self.rq_length_scale,
            gauss_length_scale=self.gauss_length_scale,
            require_gauss_confirm=self.require_gauss_confirm,
            require_gauss_crossover=self.require_gauss_crossover,
            use_volatility_filter=self.use_volatility_filter,
            min_atr_pct=self.min_atr_pct,
            use_regime_filter=self.use_regime_filter,
            regime_bars=self.regime_bars,
            use_adx_filter=self.use_adx_filter,
            min_adx=self.min_adx,
            use_ema_filter=self.use_ema_filter,
            use_sma_filter=self.use_sma_filter,
            sma_filter_period=self.sma_filter_period,
            tp_atr_mult=self.tp_atr_mult,
            sl_atr_mult=self.sl_atr_mult,
        )
        if not isinstance(ind, dict):
            return

        sig = float(ind.get("signal", float("nan")))
        atr = float(ind.get("atr", float("nan")))
        rq_trend = float(ind.get("rq_trend", float("nan")))
        if not (_is_finite(sig) and _is_finite(atr) and _is_finite(rq_trend)):
            return

        # 포지션이 있는데 trade_state가 없으면 생성(재시작/복구 케이스)
        self._ensure_trade_state(ctx, atr)
        if self._trade is not None and abs(ctx.position_size) > 1e-12 and bool(self.dynamic_exit_on_kernel_reversal):
            # 커널 추세가 포지션 방향과 반대로 뒤집히면 동적 청산
            if (ctx.position_size > 0 and rq_trend < 0) or (ctx.position_size < 0 and rq_trend > 0):
                if not self.is_closing:
                    self.is_closing = True
                    self._trade.exit_pending = True
                    self._trade.last_exit_reason = "KERNEL_REVERSAL"
                    ctx.close_position(reason="Dynamic Exit (kernel reversal)")
                    return

        if self._trade is not None and abs(ctx.position_size) > 1e-12:
            self._trade.bars_held += 1

        # ===== 시그널 기반 진입/청산 =====
        if ctx.position_size > 0 and not self.is_closing:
            if sig < 0:
                self.is_closing = True
                if self._trade is not None:
                    self._trade.exit_pending = True
                    self._trade.last_exit_reason = "SIGNAL_FLIP"
                ctx.close_position(reason="Signal Exit Long (validated bearish)")
                return

        if ctx.position_size < 0 and not self.is_closing:
            if sig > 0:
                self.is_closing = True
                if self._trade is not None:
                    self._trade.exit_pending = True
                    self._trade.last_exit_reason = "SIGNAL_FLIP"
                ctx.close_position(reason="Signal Exit Short (validated bullish)")
                return

        if abs(ctx.position_size) < 1e-12:
            if sig > 0:
                ctx.enter_long(reason="Entry Long (ML+Kernel validated)", entry_pct=self.entry_pct)
                # 진입 직후 TP/SL 레벨 확정
                self._ensure_trade_state(ctx, atr)
                return
            if sig < 0:
                ctx.enter_short(reason="Entry Short (ML+Kernel validated)", entry_pct=self.entry_pct)
                self._ensure_trade_state(ctx, atr)
                return
