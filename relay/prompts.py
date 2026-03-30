"""System prompts for LLM strategy generation."""

import ast
import textwrap
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

INTAKE_SYSTEM_PROMPT = """You are a trading strategy specification assistant.
Your job is to parse the user's natural-language request into a structured JSON specification for a trading strategy.

Output a JSON object with these fields:
- strategy_name: string (PascalCase ending in "Strategy", e.g. "RSIOversoldBounceStrategy")
- description: string (one-sentence Korean summary of the strategy)
- symbol: string (default "BTCUSDT")
- timeframe: string (default "15m", options: "1m","5m","15m","1h","4h","1d")
- direction: "long_only" | "short_only" | "long_short"
- indicators: array of strings (e.g. ["RSI(period=14)", "EMA(period=20)"])
- entry_conditions: string (precise entry logic)
- exit_conditions: string (precise exit logic)
- risk_params: object (stop_loss_pct, take_profit_pct if mentioned)
- tunable_params: object (param_name -> default_value)
- notes: string (special considerations, empty if none)

Rules:
- If the user doesn't specify symbol, default to "BTCUSDT"
- If the user doesn't specify timeframe, default to "15m"
- If the user mentions specific numbers, use them exactly
- Extract ALL mentioned indicators and their parameters
- Respond ONLY with the JSON object, no explanation
"""

SUMMARY_SYSTEM_PROMPT = """You are a trading strategy summarizer. Given a Python trading strategy's source code, produce a concise Korean summary.

Output format:
- 전략명: [strategy class name]
- 방향: [롱/숏/양방향]
- 사용 지표: [comma-separated indicator list with params]
- 진입 조건: [1-2 sentence entry logic]
- 청산 조건: [1-2 sentence exit logic]
- 특이사항: [any notable features, empty if none]

Rules:
- Keep total summary under 200 characters
- Use Korean for all descriptions
- Be specific about indicator parameters and threshold values
- Respond ONLY with the summary, no additional commentary
"""

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
        # Extract on_bar method body for compactness; fall back to full code
        on_bar_src = _extract_on_bar(code)
        code_section = (
            f"Strategy on_bar logic:\n```python\n{on_bar_src}\n```"
            if on_bar_src
            else f"Strategy code:\n```python\n{code}\n```"
        )
        return (
            f"{_CHAT_INTERFACE_REFERENCE}\n\n"
            f"{code_section}\n\n"
            f"Summary:\n{summary or 'N/A'}"
            f"{backtest_analysis_instruction}"
        )
    return (
        "You are a trading strategy expert assistant. "
        "Answer the user's question about trading strategies, markets, indicators, and technical analysis. "
        "Respond in the same language as the user's message. "
        "Provide clear, informative answers. Do NOT generate Python code unless explicitly asked."
        + backtest_analysis_instruction
    )


def _extract_on_bar(code: str) -> str | None:
    """Extract the on_bar method source from strategy code using AST."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    lines = code.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "on_bar":
            start = node.lineno - 1
            end = node.end_lineno or (start + 1)
            return textwrap.dedent("".join(lines[start:end]))
    return None


_CHAT_INTERFACE_REFERENCE = """You are a trading strategy expert. When modifying or generating strategy code, follow these interface rules:

### Key Context Methods
- ctx.current_price, ctx.position_size (>0: long, <0: short, 0: none), ctx.position_entry_price
- ctx.enter_long(reason=...), ctx.enter_short(reason=...), ctx.close_position(reason=...)
- ctx.get_indicator(name, period=..., **kwargs) -> float | dict
- ctx.get_open_orders() -> list
- ctx.calc_entry_quantity(entry_pct=None) -> float

### Mandatory Guards (on_bar)
1. `if ctx.get_open_orders(): return`
2. `if not bar.get("is_new_bar", True): return` (unless run_on_tick)
3. Check ctx.position_size before entry/exit

### bar dict keys
timestamp, bar_timestamp, bar_close, price, is_new_bar, volume"""


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
    """Build the fixed portion of the system prompt: interface + template skeleton + rules + params.

    This never changes between requests, so Azure OpenAI's automatic prompt caching
    will cache the KV projections for this prefix, reducing TTFT on subsequent calls.
    Uses condensed skeletons instead of full file contents to reduce token usage.
    """
    global _cached_static_system_prompt  # noqa: PLW0603
    if _cached_static_system_prompt is not None:
        return _cached_static_system_prompt

    _, skill = _load_template_and_skill()
    sections: list[str] = []

    # Condensed interface reference (key method signatures + rules only)
    sections.append(_CONDENSED_INTERFACE)

    # Condensed template skeleton instead of full 212-line template
    sections.append(_CONDENSED_TEMPLATE)

    if skill:
        sections.append(f"## Rules\n\n{skill}")

    # Condensed example (on_bar skeleton only, not full strategies)
    sections.append(_CONDENSED_EXAMPLE)

    sections.append(_STRATEGY_PARAMS_UI_PROMPT)
    _cached_static_system_prompt = "\n\n".join(sections) if sections else ""
    return _cached_static_system_prompt


_CONDENSED_INTERFACE = """## Strategy Interface (Quick Reference)

### StrategyContext methods
```python
# Properties
ctx.current_price -> float
ctx.position_size -> float  # >0: long, <0: short, 0: none
ctx.position_entry_price -> float
ctx.unrealized_pnl -> float
ctx.balance -> float

# Orders
ctx.buy(quantity, price=None, reason=None, exit_reason=None, use_chase=None)
ctx.sell(quantity, price=None, reason=None, exit_reason=None, use_chase=None)
ctx.close_position(reason=None, exit_reason=None, use_chase=None)
ctx.calc_entry_quantity(entry_pct=None, price=None) -> float
ctx.enter_long(reason=None, entry_pct=None)   # system-managed sizing
ctx.enter_short(reason=None, entry_pct=None)  # system-managed sizing
ctx.add_to_long(reason=None, entry_pct=None)  # pyramiding
ctx.add_to_short(reason=None, entry_pct=None)

# Indicators
ctx.get_indicator(name, period=..., **kwargs) -> float | dict
ctx.register_indicator(name, func)  # custom indicator in initialize()
ctx.get_open_orders() -> list  # guard: skip if non-empty
ctx.is_new_bar(bar) -> bool
```

### bar dict
```python
bar = {"timestamp": int, "bar_timestamp": int, "bar_close": float,
       "price": float, "is_new_bar": bool, "volume": float}
```

### Mandatory Guards
1. `if not bar.get("is_new_bar", True): return` — Only act on confirmed bars
2. `if ctx.get_open_orders(): return` — Skip when pending orders exist
3. Check `ctx.position_size` before entry/exit — prevent duplicates
"""

_CONDENSED_TEMPLATE = """## Template Skeleton

```python
from __future__ import annotations
import math
from typing import Any
from strategy.base import Strategy
from strategy.context import StrategyContext

STRATEGY_PARAMS: dict[str, Any] = { ... }
STRATEGY_PARAM_SCHEMA: dict[str, Any] = { ... }  # optional UI metadata

def crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current

def crossed_below(prev: float, current: float, level: float) -> bool:
    return current <= level < prev

class MyStrategy(Strategy):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        # read params from p, validate, store as self.xxx
        self.prev_value: float | None = None
        self.is_closing: bool = False
        self.indicator_config = {"RSI": {"period": self.rsi_period}}

    def initialize(self, ctx: StrategyContext) -> None:
        # Optional: register custom indicators via ctx.register_indicator(name, func)
        # For TA-Lib multi-output indicators (MACD etc):
        #   from indicator_strategy_template import register_talib_indicator_all_outputs
        #   register_talib_indicator_all_outputs(ctx, "MACD")
        self.prev_value = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # 1. Reset closing flag
        if ctx.position_size == 0:
            self.is_closing = False
        # 2. Guard: pending orders
        if ctx.get_open_orders():
            return
        # 3. Guard: new bar only (skip ticks)
        if not bool(bar.get("is_new_bar", True)):
            return
        # 4. Read indicators
        value = float(ctx.get_indicator("RSI", period=14))
        if not math.isfinite(value):
            return
        if self.prev_value is None:
            self.prev_value = value
            return
        # 5. Entry/exit logic with crossed_above/crossed_below
        # 6. Update prev on new bar
        self.prev_value = value
```

### Helper: register_talib_indicator_all_outputs(ctx, name)
Import from `indicator_strategy_template` for multi-output TA-Lib indicators (MACD, Stochastic, etc.).
Returns dict[str, float] instead of single float. Example: `{"macd": 0.5, "macdsignal": 0.3, "macdhist": 0.2}`

### run_on_tick pattern
For strategies that need tick-level execution (e.g., take-profit on every tick):
- Remove `if not bool(bar.get("is_new_bar", True)): return` guard
- Add `self.run_on_tick = True` in `__init__`
- Use `bar["price"]` for real-time price, `bar["bar_close"]` for closed bar price
"""

_CONDENSED_EXAMPLE = """## Example: RSI Long/Short (condensed on_bar)

```python
def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
    if ctx.position_size == 0:
        self.is_closing = False
    if ctx.get_open_orders():
        return
    if not bool(bar.get("is_new_bar", True)):
        return
    rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
    if not math.isfinite(rsi):
        return
    if self.prev_rsi is None:
        self.prev_rsi = rsi
        return
    # Long exit
    if ctx.position_size > 0 and not self.is_closing:
        if crossed_above(self.prev_rsi, rsi, self.long_exit_rsi):
            self.is_closing = True
            ctx.close_position(exit_reason="RSI overbought exit")
    # Short exit
    if ctx.position_size < 0 and not self.is_closing:
        if crossed_below(self.prev_rsi, rsi, self.short_exit_rsi):
            self.is_closing = True
            ctx.close_position(exit_reason="RSI oversold exit")
    # Long entry
    if ctx.position_size == 0:
        if crossed_above(self.prev_rsi, rsi, self.long_entry_rsi):
            ctx.enter_long(reason=f"RSI crossed above {self.long_entry_rsi}")
    # Short entry
    if ctx.position_size == 0:
        if crossed_below(self.prev_rsi, rsi, self.short_entry_rsi):
            ctx.enter_short(reason=f"RSI crossed below {self.short_entry_rsi}")
    self.prev_rsi = rsi
```
"""


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
