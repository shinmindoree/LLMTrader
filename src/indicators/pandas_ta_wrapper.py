"""pandas-ta 라이브러리 래퍼.

pandas-ta를 사용하여 지표를 계산하고, Context 인터페이스와 호환되는 형태로 반환합니다.
"""

from typing import Any

import pandas as pd
import pandas_ta as ta


class PandasTAWrapper:
    """pandas-ta 라이브러리 래퍼 클래스."""

    @staticmethod
    def _prepare_dataframe(
        closes: list[float],
        highs: list[float] | None = None,
        lows: list[float] | None = None,
        opens: list[float] | None = None,
        volumes: list[float] | None = None,
    ) -> pd.DataFrame:
        """가격 데이터를 pandas DataFrame으로 변환.

        Args:
            closes: 종가 리스트
            highs: 고가 리스트 (선택)
            lows: 저가 리스트 (선택)
            opens: 시가 리스트 (선택)
            volumes: 거래량 리스트 (선택)

        Returns:
            OHLCV DataFrame
        """
        df = pd.DataFrame({"close": closes})

        if highs is not None and len(highs) == len(closes):
            df["high"] = highs
        if lows is not None and len(lows) == len(closes):
            df["low"] = lows
        if opens is not None and len(opens) == len(closes):
            df["open"] = opens
        if volumes is not None and len(volumes) == len(closes):
            df["volume"] = volumes

        return df

    @staticmethod
    def rsi(closes: list[float], period: int = 14) -> float:
        """RSI (Relative Strength Index) 계산.

        Args:
            closes: 종가 리스트
            period: RSI 기간 (기본값: 14)

        Returns:
            RSI 값 (0~100)
        """
        if len(closes) < period + 1:
            return 50.0

        df = PandasTAWrapper._prepare_dataframe(closes)
        result = ta.rsi(df["close"], length=period)

        if result is None or result.empty or pd.isna(result.iloc[-1]):
            return 50.0

        return float(result.iloc[-1])

    @staticmethod
    def macd(
        closes: list[float],
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[float, float, float]:
        """MACD (Moving Average Convergence Divergence) 계산.

        Args:
            closes: 종가 리스트
            fast: 빠른 이동평균 기간 (기본값: 12)
            slow: 느린 이동평균 기간 (기본값: 26)
            signal: 시그널 기간 (기본값: 9)

        Returns:
            (MACD, Signal, Histogram) 튜플
        """
        if len(closes) < slow + signal:
            return (0.0, 0.0, 0.0)

        df = PandasTAWrapper._prepare_dataframe(closes)
        result = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)

        if result is None or result.empty:
            return (0.0, 0.0, 0.0)

        # pandas-ta MACD는 DataFrame을 반환 (컬럼: MACD_12_26_9, MACDs_12_26_9, MACDh_12_26_9)
        macd_col = result.columns[0] if len(result.columns) > 0 else None
        signal_col = result.columns[1] if len(result.columns) > 1 else None
        hist_col = result.columns[2] if len(result.columns) > 2 else None

        if macd_col is None or signal_col is None or hist_col is None:
            return (0.0, 0.0, 0.0)

        macd_val = float(result[macd_col].iloc[-1]) if not pd.isna(result[macd_col].iloc[-1]) else 0.0
        signal_val = float(result[signal_col].iloc[-1]) if not pd.isna(result[signal_col].iloc[-1]) else 0.0
        hist_val = float(result[hist_col].iloc[-1]) if not pd.isna(result[hist_col].iloc[-1]) else 0.0

        return (macd_val, signal_val, hist_val)

    @staticmethod
    def bollinger_bands(
        closes: list[float],
        period: int = 20,
        std_dev: float = 2.0,
    ) -> tuple[float, float, float]:
        """Bollinger Bands 계산.

        Args:
            closes: 종가 리스트
            period: 이동평균 기간 (기본값: 20)
            std_dev: 표준편차 배수 (기본값: 2.0)

        Returns:
            (Upper, Middle, Lower) 튜플
        """
        if len(closes) < period:
            return (closes[-1] if closes else 0.0, closes[-1] if closes else 0.0, closes[-1] if closes else 0.0)

        df = PandasTAWrapper._prepare_dataframe(closes)
        result = ta.bbands(df["close"], length=period, std=std_dev)

        if result is None or result.empty:
            return (closes[-1], closes[-1], closes[-1])

        # pandas-ta BB는 DataFrame을 반환 (컬럼: BBU_20_2.0, BBM_20_2.0, BBL_20_2.0)
        upper_col = result.columns[0] if len(result.columns) > 0 else None
        middle_col = result.columns[1] if len(result.columns) > 1 else None
        lower_col = result.columns[2] if len(result.columns) > 2 else None

        if upper_col is None or middle_col is None or lower_col is None:
            return (closes[-1], closes[-1], closes[-1])

        upper = float(result[upper_col].iloc[-1]) if not pd.isna(result[upper_col].iloc[-1]) else closes[-1]
        middle = float(result[middle_col].iloc[-1]) if not pd.isna(result[middle_col].iloc[-1]) else closes[-1]
        lower = float(result[lower_col].iloc[-1]) if not pd.isna(result[lower_col].iloc[-1]) else closes[-1]

        return (upper, middle, lower)

    @staticmethod
    def atr(
        highs: list[float],
        lows: list[float],
        closes: list[float],
        period: int = 14,
    ) -> float:
        """ATR (Average True Range) 계산.

        Args:
            highs: 고가 리스트
            lows: 저가 리스트
            closes: 종가 리스트
            period: ATR 기간 (기본값: 14)

        Returns:
            ATR 값
        """
        if len(closes) < period + 1 or len(highs) != len(closes) or len(lows) != len(closes):
            return 0.0

        df = PandasTAWrapper._prepare_dataframe(closes, highs=highs, lows=lows)
        result = ta.atr(df["high"], df["low"], df["close"], length=period)

        if result is None or result.empty or pd.isna(result.iloc[-1]):
            return 0.0

        return float(result.iloc[-1])

    @staticmethod
    def stochastic(
        highs: list[float],
        lows: list[float],
        closes: list[float],
        k_period: int = 14,
        d_period: int = 3,
    ) -> tuple[float, float]:
        """Stochastic Oscillator 계산.

        Args:
            highs: 고가 리스트
            lows: 저가 리스트
            closes: 종가 리스트
            k_period: %K 기간 (기본값: 14)
            d_period: %D 기간 (기본값: 3)

        Returns:
            (%K, %D) 튜플
        """
        if len(closes) < k_period + d_period or len(highs) != len(closes) or len(lows) != len(closes):
            return (50.0, 50.0)

        df = PandasTAWrapper._prepare_dataframe(closes, highs=highs, lows=lows)
        result = ta.stoch(df["high"], df["low"], df["close"], k=k_period, d=d_period)

        if result is None or result.empty:
            return (50.0, 50.0)

        # pandas-ta Stochastic은 DataFrame을 반환 (컬럼: STOCHk_14_3_3, STOCHd_14_3_3)
        k_col = result.columns[0] if len(result.columns) > 0 else None
        d_col = result.columns[1] if len(result.columns) > 1 else None

        if k_col is None or d_col is None:
            return (50.0, 50.0)

        k_val = float(result[k_col].iloc[-1]) if not pd.isna(result[k_col].iloc[-1]) else 50.0
        d_val = float(result[d_col].iloc[-1]) if not pd.isna(result[d_col].iloc[-1]) else 50.0

        return (k_val, d_val)

    @staticmethod
    def obv(closes: list[float], volumes: list[float]) -> float:
        """OBV (On Balance Volume) 계산.

        Args:
            closes: 종가 리스트
            volumes: 거래량 리스트

        Returns:
            OBV 값
        """
        if len(closes) < 2 or len(volumes) != len(closes):
            return 0.0

        df = PandasTAWrapper._prepare_dataframe(closes, volumes=volumes)
        result = ta.obv(df["close"], df["volume"])

        if result is None or result.empty or pd.isna(result.iloc[-1]):
            return 0.0

        return float(result.iloc[-1])
