"""Capability registry for strategy generation intake."""

from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_DATA_SOURCES: tuple[str, ...] = (
    "Binance OHLCV (candlestick) price/volume data",
)

SUPPORTED_INDICATOR_SCOPES: tuple[str, ...] = (
    "TA-Lib builtin indicators via ctx.get_indicator(...)",
    "Custom indicator functions defined in strategy file via ctx.register_indicator(...)",
)

SUPPORTED_CONTEXT_METHODS: tuple[str, ...] = (
    "current_price",
    "position_size",
    "position_entry_price",
    "unrealized_pnl",
    "balance",
    "buy",
    "sell",
    "close_position",
    "calc_entry_quantity",
    "enter_long",
    "enter_short",
    "get_indicator",
    "register_indicator",
    "get_open_orders",
)


@dataclass(frozen=True)
class CapabilityRule:
    """Keyword-based unsupported capability rule."""

    name: str
    keywords: tuple[str, ...]
    user_message: str


UNSUPPORTED_CAPABILITY_RULES: tuple[CapabilityRule, ...] = (
    CapabilityRule(
        name="social_stream",
        keywords=("twitter", "tweet", "x.com", "트윗", "소셜", "social"),
        user_message="실시간 소셜 데이터 수집/연동 파이프라인이 필요합니다.",
    ),
    CapabilityRule(
        name="news_feed",
        keywords=("news", "headline", "뉴스", "기사"),
        user_message="외부 뉴스 데이터 수집/연동 파이프라인이 필요합니다.",
    ),
    CapabilityRule(
        name="sentiment_engine",
        keywords=("sentiment", "감성", "nlp", "긍정", "부정"),
        user_message="외부 감성분석 파이프라인 연동이 필요합니다.",
    ),
    CapabilityRule(
        name="onchain_feed",
        keywords=("onchain", "온체인"),
        user_message="온체인 데이터 수집/연동 파이프라인이 필요합니다.",
    ),
    CapabilityRule(
        name="macro_feed",
        keywords=("fomc", "cpi", "금리", "거시", "macro"),
        user_message="거시경제 데이터 수집/연동 파이프라인이 필요합니다.",
    ),
)


UNSUPPORTED_CONTEXT_METHOD_HINTS: tuple[tuple[str, str], ...] = (
    ("get_latest_event", "StrategyContext에는 get_latest_event()가 없습니다."),
    ("get_news", "StrategyContext에는 get_news()가 없습니다."),
    ("get_tweet", "StrategyContext에는 get_tweet()가 없습니다."),
    ("fetch_tweet", "전략 코드에서 외부 트윗 API 직접 호출은 현재 지원하지 않습니다."),
    ("fetch_news", "전략 코드에서 외부 뉴스 API 직접 호출은 현재 지원하지 않습니다."),
    ("get_orderbook", "StrategyContext에는 get_orderbook()가 없습니다."),
    ("get_funding_rate", "StrategyContext에는 get_funding_rate()가 없습니다."),
    ("get_open_interest", "StrategyContext에는 get_open_interest()가 없습니다."),
)


def detect_unsupported_requirements(text: str) -> list[str]:
    """Detect unsupported requirements from free-form text."""
    normalized = (text or "").lower()
    if not normalized:
        return []

    found: list[str] = []
    for rule in UNSUPPORTED_CAPABILITY_RULES:
        if any(keyword in normalized for keyword in rule.keywords):
            if rule.user_message not in found:
                found.append(rule.user_message)

    for token, message in UNSUPPORTED_CONTEXT_METHOD_HINTS:
        if token.lower() in normalized and message not in found:
            found.append(message)

    return found


def capability_summary_lines() -> list[str]:
    """Human-readable capability summary lines."""
    methods = ", ".join(SUPPORTED_CONTEXT_METHODS)
    data_sources = ", ".join(SUPPORTED_DATA_SOURCES)
    indicators = ", ".join(SUPPORTED_INDICATOR_SCOPES)
    return [
        f"지원 데이터 소스: {data_sources}",
        f"지원 인디케이터 범위: {indicators}",
        f"지원 StrategyContext 항목: {methods}",
    ]


def capability_prompt_fragment() -> str:
    """Prompt fragment for injecting capability boundaries."""
    supported_data = "; ".join(SUPPORTED_DATA_SOURCES)
    supported_indicators = "; ".join(SUPPORTED_INDICATOR_SCOPES)
    methods = ", ".join(SUPPORTED_CONTEXT_METHODS)
    unsupported_names = ", ".join(rule.name for rule in UNSUPPORTED_CAPABILITY_RULES)
    return (
        "Current capability registry:\n"
        f"- Supported data sources: {supported_data}\n"
        f"- Supported indicator scope: {supported_indicators}\n"
        f"- Supported StrategyContext methods: {methods}\n"
        f"- Unsupported capability categories (without extra infra): {unsupported_names}"
    )
