"""System prompts for LLM strategy generation."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "indicator_strategy_template.py"
_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "indicator-strategy" / "SKILL.md"
_VERIFY_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "strategy-verify" / "SKILL.md"
_STRATEGIES_DIR = _REPO_ROOT / "scripts" / "strategies"

_BASE_PATH = _REPO_ROOT / "src" / "strategy" / "base.py"
_CONTEXT_PATH = _REPO_ROOT / "src" / "strategy" / "context.py"
_AGENTS_MD_PATH = _REPO_ROOT / "src" / "strategy" / "AGENTS.md"

_DEFAULT_EXAMPLES: tuple[Path, ...] = (
    _REPO_ROOT / "scripts" / "strategies" / "rsi_long_short_strategy.py",
    _REPO_ROOT / "scripts" / "strategies" / "macd_hist_immediate_entry_takeprofit_strategy.py",
)

_INDICATOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "rsi": ("rsi", "relative strength", "과매수", "과매도", "oversold", "overbought"),
    "macd": ("macd", "이동평균수렴", "히스토그램", "histogram"),
    "ema": ("ema", "지수이동평균", "이동평균", "이평선", "exponential moving"),
    "bollinger": ("bollinger", "볼린저", "bb"),
    "stochastic": ("stochastic", "스토캐스틱", "stoch"),
    "williams": ("williams", "윌리엄스"),
    "turtle": ("turtle", "터틀", "돌파", "breakout"),
    "scalping": ("scalp", "스캘핑", "초단타"),
    "crossover": ("crossover", "크로스", "교차", "골든크로스", "데드크로스"),
    "momentum": ("momentum", "모멘텀"),
    "takeprofit": ("takeprofit", "익절", "take profit", "tp"),
}

INTAKE_SYSTEM_PROMPT = ""

SUMMARY_SYSTEM_PROMPT = ""

TEST_SYSTEM_PROMPT = ""

PLANNER_SYSTEM_PROMPT = """You are a trading strategy architect.
Analyze the user's request and produce a detailed implementation specification as JSON.

FIRST, determine whether the request is related to trading/investing strategy.

Output a JSON object. Always include these three fields first:
- is_trading_related: boolean — true if the request is about creating, modifying, or discussing a trading/investing strategy. false for anything unrelated (casual chat, weather, jokes, coding help unrelated to trading, etc.)
- rejection_message: string — if is_trading_related is false, write a brief, friendly Korean message explaining that this system is for trading strategy generation only and suggesting they describe a trading strategy. Empty string if is_trading_related is true.
- intent: string — one of "modify" or "question". "modify" if the user wants to create a new strategy or change/improve an existing one and code generation is needed. "question" if the user is asking about the strategy, requesting explanation, or discussing without needing code changes.

If is_trading_related is true AND intent is "modify", also include:
- strategy_name: PascalCase class name ending in "Strategy" (e.g. "RSIOversoldBounceStrategy")
- description: One-sentence summary of the strategy
- symbol: Trading pair (default "BTCUSDT" if unspecified)
- timeframe: Candle interval (default "15m" if unspecified)
- direction: "long_only", "short_only", or "long_short"
- indicators: Array of indicators with parameters, e.g. ["RSI(period=14)", "EMA(period=20)"]
- entry_long: Precise long entry condition (empty string if not applicable)
- entry_short: Precise short entry condition (empty string if not applicable)
- exit_long: Precise long exit condition
- exit_short: Precise short exit condition
- risk_management: Position sizing and stop-loss description
- tunable_params: Object mapping parameter names to default values, e.g. {"rsi_period": 14, "oversold": 30}
- notes: Any special implementation considerations

Available indicators: RSI, MACD, EMA, SMA, Bollinger Bands, Stochastic, Williams %R, ATR, ADX, CCI, OBV, VWAP.
Available context: OHLCV candles, current position, open orders, account balance.
Guard requirements: Must check is_new_bar before logic; must call get_open_orders before placing orders.
"""

_STRATEGY_PARAMS_UI_PROMPT = """## Tunable parameters (product UI)
At **module level** (before the Strategy subclass), define:
- `STRATEGY_PARAMS`: `dict[str, int | float | bool | str]` — every user-tunable default the UI may edit.
- Optional `STRATEGY_PARAM_SCHEMA`: same keys → metadata for the UI: `type` (`integer`|`number`|`boolean`|`string`), `label`, and optionally `min` / `max` / `enum` / `description` / `group`.

Schema field conventions:
- `label`: Short parameter name (Korean), e.g. "RSI 기간"
- `description`: One sentence explaining how this parameter affects the strategy logic (Korean). Example: "RSI가 이 값 아래로 내려가면 롱 진입 신호가 발생합니다. 값이 낮을수록 더 극단적인 과매도 구간에서만 진입합니다."
- `group`: Category for grouping in the UI. Use exactly one of: "진입 (Entry)", "청산 (Exit)", "지표 (Indicator)", "리스크 관리 (Risk)", "일반 (General)". Every parameter MUST have a group.

The strategy class **must** use `def __init__(self, **kwargs: Any) -> None:` and merge defaults with `merged = {**STRATEGY_PARAMS, **kwargs}`, then read all tunables from `merged` only (no duplicate magic numbers). Job runners may call `StrategyClass()` or `StrategyClass(**overrides)`.

Reference: `scripts/strategies/rsi_long_short_strategy.py`.
"""


def build_strategy_chat_system_prompt(code: str, summary: str | None) -> str:
    backtest_analysis_instruction = (
        "\n\nWhen the user provides backtest results (e.g., return%, win rate, max drawdown, "
        "Sharpe ratio, trade counts), you MUST:\n"
        "1. Analyze the key metrics and identify strengths and weaknesses\n"
        "2. Explain why the strategy may be underperforming (if applicable)\n"
        "3. Suggest specific parameter changes or logic improvements with rationale\n"
        "4. If the user asks for improvement, generate the full improved strategy code\n"
        "5. Focus on actionable advice: concrete numbers for parameter changes, specific conditions to add/modify\n"
        "Respond in the same language as the user's message."
    )
    if code and code.strip():
        return f"Strategy code:\n{code}\n\nSummary:\n{summary or 'N/A'}{backtest_analysis_instruction}"
    return (
        "You are a trading strategy expert assistant. "
        "Answer the user's question about trading strategies, markets, indicators, and technical analysis. "
        "Respond in the same language as the user's message. "
        "Provide clear, informative answers. Do NOT generate Python code unless explicitly asked."
        + backtest_analysis_instruction
    )


def _read_file(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _load_template_and_skill() -> tuple[str | None, str | None]:
    return _read_file(_TEMPLATE_PATH), _read_file(_SKILL_PATH)


def _load_verify_skill() -> str | None:
    return _read_file(_VERIFY_SKILL_PATH)


def _load_interface_docs() -> str:
    sections: list[str] = []
    base = _read_file(_BASE_PATH)
    if base:
        sections.append(f"### strategy/base.py\n\n```python\n{base}\n```")
    context = _read_file(_CONTEXT_PATH)
    if context:
        sections.append(f"### strategy/context.py\n\n```python\n{context}\n```")
    agents_md = _read_file(_AGENTS_MD_PATH)
    if agents_md:
        sections.append(f"### Strategy Interface Guide\n\n{agents_md}")
    return "\n\n".join(sections)


def _select_example_strategies(user_prompt: str, max_examples: int = 3) -> list[Path]:
    if not _STRATEGIES_DIR.is_dir():
        return [p for p in _DEFAULT_EXAMPLES if p.exists()]

    all_strategies = sorted(_STRATEGIES_DIR.glob("*_strategy.py"))
    if not all_strategies:
        return [p for p in _DEFAULT_EXAMPLES if p.exists()]

    prompt_lower = (user_prompt or "").lower()
    if not prompt_lower:
        return [p for p in _DEFAULT_EXAMPLES if p.exists()]

    scored: list[tuple[int, Path]] = []
    for path in all_strategies:
        name_lower = path.stem.lower()
        score = 0
        for indicator, keywords in _INDICATOR_KEYWORDS.items():
            if any(kw in prompt_lower for kw in keywords):
                if indicator in name_lower:
                    score += 3
        name_parts = name_lower.replace("_strategy", "").split("_")
        score += sum(1 for part in name_parts if len(part) > 2 and part in prompt_lower)
        scored.append((score, path))

    scored.sort(key=lambda x: (-x[0], x[1].name))

    selected = [path for score, path in scored if score > 0][:max_examples]

    for default in _DEFAULT_EXAMPLES:
        if len(selected) >= max_examples:
            break
        if default not in selected and default.exists():
            selected.append(default)

    if not selected:
        selected = [p for p in _DEFAULT_EXAMPLES if p.exists()]

    return selected


def _load_example_strategies(user_prompt: str = "") -> str:
    paths = (
        _select_example_strategies(user_prompt)
        if user_prompt
        else [p for p in _DEFAULT_EXAMPLES if p.exists()]
    )
    parts: list[str] = []
    for path in paths:
        content = _read_file(path)
        if content:
            parts.append(f"### {path.name}\n\n{content}")
    return "\n\n".join(parts)


# --- Cached static system prompt (identical across all requests → Azure OpenAI prompt caching) ---

_cached_static_system_prompt: str | None = None


def _build_static_system_prompt() -> str:
    """Build the fixed portion of the system prompt: interface + template + rules + params.

    This never changes between requests, so Azure OpenAI's automatic prompt caching
    will cache the KV projections for this prefix, reducing TTFT on subsequent calls.
    """
    global _cached_static_system_prompt  # noqa: PLW0603
    if _cached_static_system_prompt is not None:
        return _cached_static_system_prompt

    template, skill = _load_template_and_skill()
    interface = _load_interface_docs()
    sections: list[str] = []
    if interface:
        sections.append(f"## Strategy Interface\n\n{interface}")
    if template:
        sections.append(f"## Template\n\n{template}")
    if skill:
        sections.append(f"## Rules\n\n{skill}")
    # Default examples are always included in the static portion for cache consistency
    default_examples = "\n\n".join(
        f"### {p.name}\n\n{_read_file(p)}"
        for p in _DEFAULT_EXAMPLES
        if p.exists() and _read_file(p)
    )
    if default_examples:
        sections.append(f"## Examples\n\n{default_examples}")
    sections.append(_STRATEGY_PARAMS_UI_PROMPT)
    _cached_static_system_prompt = "\n\n".join(sections) if sections else ""
    return _cached_static_system_prompt


def build_system_prompt(user_prompt: str = "") -> str:
    static = _build_static_system_prompt()
    # Only append extra prompt-relevant examples if they differ from defaults
    if user_prompt:
        extra_paths = _select_example_strategies(user_prompt)
        extra_parts: list[str] = []
        for path in extra_paths:
            if path in _DEFAULT_EXAMPLES:
                continue  # already in static prompt
            content = _read_file(path)
            if content:
                extra_parts.append(f"### {path.name}\n\n{content}")
        if extra_parts:
            return static + "\n\n## Additional Examples\n\n" + "\n\n".join(extra_parts)
    return static


def build_intake_system_prompt() -> str:
    return INTAKE_SYSTEM_PROMPT


def build_planner_system_prompt() -> str:
    return PLANNER_SYSTEM_PROMPT


def build_repair_system_prompt() -> str:
    static = _build_static_system_prompt()
    verify_skill = _load_verify_skill()
    if verify_skill:
        return static + "\n\n" + verify_skill
    return static
