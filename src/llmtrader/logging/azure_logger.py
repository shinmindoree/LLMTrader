"""Azure Application Insights 로거.

로그를 Azure에 전송하여:
1. 실시간 에러 감지 및 알림
2. 장기 로그 저장 및 쿼리
3. 대시보드 및 메트릭 분석
"""

import logging
import os
import sys
from datetime import datetime
from typing import Any

# Azure OpenTelemetry 통합 (선택적)
try:
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    configure_azure_monitor = None  # type: ignore
    trace = None  # type: ignore


class AzureLogger:
    """Azure Application Insights 통합 로거.

    기능:
    - 콘솔 출력 (기존 동작 유지)
    - Azure Application Insights로 구조화된 로그 전송
    - 에러 자동 추적 및 예외 상세 정보
    - 커스텀 이벤트/메트릭 전송
    """

    def __init__(
        self,
        name: str = "llmtrader",
        connection_string: str | None = None,
        console_output: bool = True,
        log_level: int = logging.INFO,
    ) -> None:
        """로거 초기화.

        Args:
            name: 로거 이름
            connection_string: Azure Application Insights 연결 문자열
            console_output: 콘솔 출력 여부
            log_level: 로그 레벨
        """
        self.name = name
        self.connection_string = connection_string or os.getenv(
            "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
        )
        self.console_output = console_output
        self.log_level = log_level
        self._azure_configured = False
        self._tracer = None

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

        # Azure Application Insights 설정
        self._setup_azure()

    def _setup_azure(self) -> None:
        """Azure Application Insights 연결 설정."""
        if not AZURE_AVAILABLE:
            self.logger.warning(
                "Azure Monitor SDK not installed. Run: uv add azure-monitor-opentelemetry"
            )
            return

        if not self.connection_string:
            self.logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set. Azure logging disabled.")
            return

        try:
            configure_azure_monitor(
                connection_string=self.connection_string,
                logger_name=self.name,
            )
            self._tracer = trace.get_tracer(self.name)
            self._azure_configured = True
            self.logger.info("Azure Application Insights configured successfully.")
        except Exception as e:
            self.logger.warning(f"Failed to configure Azure Monitor: {e}")

    @property
    def is_azure_enabled(self) -> bool:
        """Azure 로깅 활성화 여부."""
        return self._azure_configured

    def info(self, message: str, **extra: Any) -> None:
        """INFO 레벨 로그."""
        self._log(logging.INFO, message, extra)

    def warning(self, message: str, **extra: Any) -> None:
        """WARNING 레벨 로그."""
        self._log(logging.WARNING, message, extra)

    def error(self, message: str, exc_info: bool = False, **extra: Any) -> None:
        """ERROR 레벨 로그 (Azure에서 자동 알림 트리거 가능)."""
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
        """로그 메시지 출력 및 Azure 전송."""
        # 구조화된 데이터를 메시지에 포함
        if extra:
            extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
            full_message = f"{message} | {extra_str}"
        else:
            full_message = message

        # Python 로거로 출력 (콘솔 + Azure 핸들러)
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
        """틱 데이터 로그 (1초마다 호출, INFO 레벨).

        Azure에서 쿼리 예시:
        traces | where message contains "TICK" | project timestamp, customDimensions
        """
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
        """주문 이벤트 로그 (WARNING 레벨로 눈에 띄게).

        Azure에서 쿼리 예시:
        traces | where message == "ORDER" | where customDimensions.event == "ENTRY"
        """
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
        """에러 로그 (Azure Alert 트리거 대상).

        Azure에서 Alert Rule 설정:
        traces | where severityLevel >= 3 | where message == "TRADE_ERROR"
        """
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
        """전략 신호 로그 (분석용).

        Azure에서 쿼리 예시:
        traces | where message == "SIGNAL" | summarize count() by customDimensions.signal
        """
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


def get_logger(name: str = "llmtrader", connection_string: str | None = None) -> AzureLogger:
    """로거 인스턴스 반환.
    
    Args:
        name: 로거 이름
        connection_string: Azure 연결 문자열 (None이면 환경변수/설정에서 로드)
    """
    # Settings에서 연결 문자열 로드 시도
    if connection_string is None:
        try:
            from llmtrader.settings import get_settings
            connection_string = get_settings().azure.connection_string
        except Exception:  # noqa: BLE001
            pass
    
    return AzureLogger(name=name, connection_string=connection_string)

