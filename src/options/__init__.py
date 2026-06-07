"""Binance European Options 패키지 (Phase 0 PoC).

Binance European Options API (``eapi.binance.com``) 연동을 위한 모듈.

테스트넷 도메인 안내:
- 메인넷: ``https://eapi.binance.com``
- 테스트넷: ``https://testnet.binancefuture.com`` (퓨처스 테스트넷과 공유)
"""

from options.symbol import (
    OptionSide,
    OptionSymbol,
    format_option_symbol,
    parse_option_symbol,
)

__all__ = [
    "OptionSide",
    "OptionSymbol",
    "format_option_symbol",
    "parse_option_symbol",
]
