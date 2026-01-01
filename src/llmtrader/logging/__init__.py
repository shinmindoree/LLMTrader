"""간단한 콘솔 로깅 모듈."""

from llmtrader.logging.azure_logger import AzureLogger, get_logger

# 호환성을 위한 별칭
SimpleLogger = AzureLogger

__all__ = ["AzureLogger", "SimpleLogger", "get_logger"]

