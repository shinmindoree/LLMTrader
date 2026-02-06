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

CAPABILITY_CATEGORY_LABELS: dict[str, str] = {
    "social_stream": "소셜 스트림",
    "news_feed": "뉴스 피드",
    "sentiment_engine": "감성 엔진",
    "onchain_feed": "온체인 피드",
    "macro_feed": "거시 지표 피드",
}

CAPABILITY_DEVELOPMENT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "social_stream": (
        "X(Twitter) 수집 커넥터 + API 인증키 관리",
        "실시간/히스토리 트윗 저장 스키마 설계 (timestamp, author, text)",
        "심볼/자산 매핑 규칙 정의 (예: BTC 관련 텍스트 필터)",
        "전략 런타임에서 읽을 수 있는 인디케이터 어댑터 구현",
    ),
    "news_feed": (
        "뉴스 공급원 API 연동 + 요청 제한/재시도 정책",
        "헤드라인 정규화/중복제거 파이프라인 구축",
        "심볼 매핑 및 이벤트 타임스탬프 정합성 처리",
        "전략 런타임 인디케이터 입력 포맷 정의",
    ),
    "sentiment_engine": (
        "텍스트 감성 점수화 모델/서비스 선택 및 배포",
        "감성 점수 스키마 정의 (-1~1 또는 확률 분포)",
        "실시간 추론 실패 시 fallback 정책 정의",
        "백테스트 재현 가능한 히스토리 감성 데이터셋 구축",
    ),
    "onchain_feed": (
        "온체인 데이터 제공자/API 선택 및 키 관리",
        "체인별 지표 수집 주기/지연 시간 기준 수립",
        "거래 심볼과 온체인 자산 매핑 테이블 구축",
        "전략 런타임에서 사용할 지표 인터페이스 구현",
    ),
    "macro_feed": (
        "거시 이벤트 캘린더/지표 API 연동",
        "발표 시각(Timezone/지연 반영) 정합성 처리",
        "서프라이즈 값(예상 대비) 계산 로직 추가",
        "전략에서 참조 가능한 이벤트 인디케이터 어댑터 구현",
    ),
}

CONTEXT_EXTENSION_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("get_latest_event", "이벤트 스토어를 구축하고 ctx.get_indicator(...) 기반 어댑터를 추가해야 합니다."),
    ("get_news", "뉴스 데이터 저장소와 ctx.get_indicator(...) 브리지 구현이 필요합니다."),
    ("get_tweet", "소셜 데이터 저장소와 ctx.get_indicator(...) 브리지 구현이 필요합니다."),
    ("fetch_tweet", "전략 코드 내부 직접 API 호출 대신 외부 수집 파이프라인 + 인디케이터 주입 구조가 필요합니다."),
    ("fetch_news", "전략 코드 내부 직접 API 호출 대신 외부 수집 파이프라인 + 인디케이터 주입 구조가 필요합니다."),
    ("get_orderbook", "오더북 수집/저장 파이프라인과 StrategyContext 확장이 필요합니다."),
    ("get_funding_rate", "펀딩비 수집/저장 파이프라인과 StrategyContext 확장이 필요합니다."),
    ("get_open_interest", "미결제약정 수집/저장 파이프라인과 StrategyContext 확장이 필요합니다."),
)


def detect_unsupported_categories(text: str) -> list[str]:
    """Detect unsupported capability category names from free-form text."""
    normalized = (text or "").lower()
    if not normalized:
        return []

    out: list[str] = []
    for rule in UNSUPPORTED_CAPABILITY_RULES:
        if any(keyword in normalized for keyword in rule.keywords):
            if rule.name not in out:
                out.append(rule.name)
    return out


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


def build_development_requirements(text: str) -> list[str]:
    """Build concrete development checklist lines for unsupported requests."""
    normalized = (text or "").lower()
    if not normalized:
        return []

    out: list[str] = []
    for category in detect_unsupported_categories(normalized):
        label = CAPABILITY_CATEGORY_LABELS.get(category, category)
        tasks = CAPABILITY_DEVELOPMENT_REQUIREMENTS.get(category, ())
        for task in tasks:
            line = f"[{label}] {task}"
            if line not in out:
                out.append(line)

    for token, requirement in CONTEXT_EXTENSION_REQUIREMENTS:
        if token.lower() in normalized:
            line = f"[런타임 확장] {requirement}"
            if line not in out:
                out.append(line)

    return out


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
