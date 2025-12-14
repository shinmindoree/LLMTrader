"""RSI (Relative Strength Index) 계산.

본 프로젝트는 바이낸스(TradingView)의 기본 RSI와 최대한 동일하게 맞추기 위해
Wilder's RSI(RMA/SMMA smoothing) 방식으로 계산한다.
"""

from __future__ import annotations


def rsi_wilder_from_closes(closes: list[float], period: int = 14) -> float:
    """Wilder(RMA) 방식 RSI 계산.

    Args:
        closes: 종가 시퀀스 (닫힌 봉 close들 또는 + 현재가(last) 포함)
        period: RSI 기간

    Returns:
        RSI 값(0~100). 데이터가 부족하면 50.0 반환.
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(closes) < period + 1:
        return 50.0

    # 변화량
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-change)

    # 초기 평균(단순평균)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing(RMA): 이후 구간은 (prev*(period-1) + current) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


