"""간단한 콘솔 로거 (Azure Application Insights 제거됨)."""

import logging
import sys
from datetime import datetime
from typing import Any


class SimpleLogger:
    """간단한 콘솔 로거."""

    def __init__(
        self,
        name: str = "llmtrader",
        console_output: bool = True,
        log_level: int = logging.INFO,
    ) -> None:
        """로거 초기화.

        Args:
            name: 로거 이름
            console_output: 콘솔 출력 여부
            log_level: 로그 레벨
        """
        self.name = name
        self.console_output = console_output
        self.log_level = log_level

        # 표준 Python 로거 설정
        self.logger = logging.getLogger(name)
        self.logger.setLevel(log_level)
        self.logger.handlers.clear()

        # 콘솔 핸들러
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(log_level)
            console_format = logging.Formatter(
                "[%(asctime)s] %(levelname)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
            console_handler.setFormatter(console_format)
            self.logger.addHandler(console_handler)

    @property
    def is_azure_enabled(self) -> bool:
        """Azure 로깅 활성화 여부 (항상 False)."""
        return False

    def info(self, message: str, **extra: Any) -> None:
        """INFO 레벨 로그."""
        self._log(logging.INFO, message, extra)

    def warning(self, message: str, **extra: Any) -> None:
        """WARNING 레벨 로그."""
        self._log(logging.WARNING, message, extra)

    def error(self, message: str, exc_info: bool = False, **extra: Any) -> None:
        """ERROR 레벨 로그."""
        self._log(logging.ERROR, message, extra, exc_info=exc_info)

    def critical(self, message: str, exc_info: bool = True, **extra: Any) -> None:
        """CRITICAL 레벨 로그."""
        self._log(logging.CRITICAL, message, extra, exc_info=exc_info)

    def debug(self, message: str, **extra: Any) -> None:
        """DEBUG 레벨 로그."""
        self._log(logging.DEBUG, message, extra)

    def _log(
        self,
        level: int,
        message: str,
        extra: dict[str, Any],
        exc_info: bool = False,
    ) -> None:
        """로그 메시지 출력."""
        # 구조화된 데이터를 메시지에 포함
        if extra:
            extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
            full_message = f"{message} | {extra_str}"
        else:
            full_message = message

        # Python 로거로 출력 (콘솔)
        self.logger.log(level, full_message, exc_info=exc_info, extra=extra)

    # ─────────────────────────────────────────────────────────────────
    # 트레이딩 전용 이벤트 메서드
    # ─────────────────────────────────────────────────────────────────

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
        """틱 데이터 로그 (1초마다 호출, INFO 레벨)."""
        self.info(
            "TICK",
            symbol=symbol,
            bar_time=bar_time,
            price=f"{price:,.2f}",
            rsi=f"{rsi:.2f}",
            rsi_rt=f"{rsi_rt:.2f}",
            position=f"{position:.4f}",
            balance=f"{balance:,.2f}",
            pnl=f"{pnl:,.2f}",
            **extra,
        )

    def log_order(
        self,
        event: str,  # "ENTRY" or "EXIT"
        symbol: str,
        side: str,
        qty: float,
        price: float,
        order_id: str,
        rsi: float,
        pnl: float | None = None,
        **extra: Any,
    ) -> None:
        """주문 이벤트 로그 (WARNING 레벨로 눈에 띄게)."""
        self.warning(
            "ORDER",
            event=event,
            symbol=symbol,
            side=side,
            qty=f"{qty:.4f}",
            price=f"{price:,.2f}",
            order_id=order_id,
            rsi=f"{rsi:.2f}",
            pnl=f"{pnl:,.2f}" if pnl is not None else "N/A",
            **extra,
        )

    def log_error(
        self,
        error_type: str,
        message: str,
        symbol: str | None = None,
        **extra: Any,
    ) -> None:
        """에러 로그."""
        self.error(
            "TRADE_ERROR",
            error_type=error_type,
            error_message=message,
            symbol=symbol or "N/A",
            exc_info=True,
            **extra,
        )

    def log_strategy_signal(
        self,
        signal: str,  # "BUY_SIGNAL", "SELL_SIGNAL", "STOP_LOSS", etc.
        symbol: str,
        rsi: float,
        price: float,
        reason: str,
        **extra: Any,
    ) -> None:
        """전략 신호 로그 (분석용)."""
        self.info(
            "SIGNAL",
            signal=signal,
            symbol=symbol,
            rsi=f"{rsi:.2f}",
            price=f"{price:,.2f}",
            reason=reason,
            **extra,
        )

    def log_risk_event(
        self,
        event: str,  # "ORDER_REJECTED", "DAILY_LIMIT_HIT", etc.
        reason: str,
        **extra: Any,
    ) -> None:
        """리스크 이벤트 로그 (WARNING 레벨)."""
        self.warning(
            "RISK_EVENT",
            event=event,
            reason=reason,
            **extra,
        )

    def log_session_start(
        self,
        symbol: str,
        strategy: str,
        leverage: int,
        max_position: float,
        **extra: Any,
    ) -> None:
        """세션 시작 로그."""
        self.warning(
            "SESSION_START",
            symbol=symbol,
            strategy=strategy,
            leverage=leverage,
            max_position=max_position,
            timestamp=datetime.utcnow().isoformat(),
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
        """세션 종료 로그 (요약 통계)."""
        self.warning(
            "SESSION_END",
            symbol=symbol,
            total_trades=total_trades,
            total_pnl=f"{total_pnl:,.2f}",
            win_rate=f"{win_rate:.1%}",
            duration_minutes=f"{duration_minutes:.1f}",
            timestamp=datetime.utcnow().isoformat(),
            **extra,
        )


def get_logger(name: str = "llmtrader", connection_string: str | None = None) -> SimpleLogger:
    """로거 인스턴스 반환.
    
    Args:
        name: 로거 이름
        connection_string: 무시됨 (호환성을 위해 유지)
    """
    return SimpleLogger(name=name)

# 호환성을 위한 별칭
AzureLogger = SimpleLogger
