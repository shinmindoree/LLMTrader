"""간단한 콘솔 로거."""

import logging as std_logging
from datetime import datetime
from typing import Any


class SimpleLogger:
    """간단한 콘솔 로거."""

    def __init__(
        self,
        name: str = "llmtrader",
        console_output: bool = True,
        log_level: int = std_logging.INFO,
    ) -> None:
        """로거 초기화.

        Args:
            name: 로거 이름
            console_output: 콘솔 출력 여부
            log_level: 로그 레벨
        """
        self.name = name
        self.console_output = console_output
        self.logger = std_logging.getLogger(name)
        self.logger.setLevel(log_level)

        if not self.logger.handlers:
            handler = std_logging.StreamHandler()
            handler.setLevel(log_level)
            formatter = std_logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def info(self, message: str, **extra: Any) -> None:
        """INFO 레벨 로그."""
        self._log(std_logging.INFO, message, extra)

    def warning(self, message: str, **extra: Any) -> None:
        """WARNING 레벨 로그."""
        self._log(std_logging.WARNING, message, extra)

    def error(self, message: str, exc_info: bool = False, **extra: Any) -> None:
        """ERROR 레벨 로그."""
        self._log(std_logging.ERROR, message, extra, exc_info=exc_info)

    def critical(self, message: str, exc_info: bool = True, **extra: Any) -> None:
        """CRITICAL 레벨 로그."""
        self._log(std_logging.CRITICAL, message, extra, exc_info=exc_info)

    def debug(self, message: str, **extra: Any) -> None:
        """DEBUG 레벨 로그."""
        self._log(std_logging.DEBUG, message, extra)

    def _log(
        self,
        level: int,
        message: str,
        extra: dict[str, Any],
        exc_info: bool = False,
    ) -> None:
        """로그 메시지 출력."""
        if extra:
            message = f"{message} | {extra}"
        self.logger.log(level, message, exc_info=exc_info)

    def log_session_start(
        self,
        symbol: str,
        strategy: str,
        leverage: int,
        max_position: float,
        **extra: Any,
    ) -> None:
        """세션 시작 로그."""
        self.info(
            f"세션 시작 | symbol={symbol}, strategy={strategy}, leverage={leverage}, max_position={max_position}",
            **extra,
        )

    def log_session_end(
        self,
        symbol: str,
        total_trades: int,
        total_pnl: float,
        win_rate: float,
        duration_minutes: float,
        **extra: Any,
    ) -> None:
        """세션 종료 로그."""
        self.info(
            f"세션 종료 | symbol={symbol}, trades={total_trades}, pnl={total_pnl:.2f}, win_rate={win_rate:.2%}, duration={duration_minutes:.1f}분",
            **extra,
        )

    def log_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        pnl: float | None = None,
        **extra: Any,
    ) -> None:
        """거래 로그."""
        msg = f"거래 | symbol={symbol}, side={side}, qty={quantity}, price={price}"
        if pnl is not None:
            msg += f", pnl={pnl:.2f}"
        self.info(msg, **extra)

    def log_error(
        self,
        error_type: str,
        message: str,
        symbol: str | None = None,
        **extra: Any,
    ) -> None:
        """에러 로그."""
        msg = f"에러 | type={error_type}, message={message}"
        if symbol:
            msg += f", symbol={symbol}"
        self.error(msg, **extra)

    def log_tick(
        self,
        symbol: str,
        bar_time: str,
        price: float,
        rsi: float,
        rsi_rt: float,
        position: float,
        balance: float,
        pnl: float,
        **extra: Any,
    ) -> None:
        """틱 데이터 로그."""
        self.info(
            f"TICK | symbol={symbol}, bar_time={bar_time}, price={price:,.2f}, rsi={rsi:.2f}, rsi_rt={rsi_rt:.2f}, position={position:.4f}, balance={balance:,.2f}, pnl={pnl:,.2f}",
            **extra,
        )


def get_logger(name: str = "llmtrader", **kwargs: Any) -> SimpleLogger:
    """로거 인스턴스 반환.

    Args:
        name: 로거 이름
        **kwargs: 추가 인자 (호환성을 위해 무시)
    """
    return SimpleLogger(name=name, **kwargs)

