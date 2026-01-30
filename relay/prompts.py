"""System prompt for strategy code generation (indicator-strategy rules + template)."""

STRATEGY_SYSTEM_PROMPT = """You generate a single Python file for a trading strategy. The file must be loadable by a runner that discovers a class whose name ends with "Strategy".

Rules:
- Output ONLY the contents of one new file: scripts/strategies/{name}_strategy.py (file path is logical; emit only the code).
- File name: snake_case, suffix _strategy.py. Class name: PascalCase ending with "Strategy" (e.g. RsiOversoldBounceLongStrategy).
- Imports: from strategy.base import Strategy; from strategy.context import StrategyContext.
- Include these helpers inside the file (do not import from template): _last_non_nan, register_talib_indicator_all_outputs, and if needed crossed_above, crossed_below.
- Class: inherit Strategy; __init__ must set self.params (dict), self.indicator_config (dict), prev_* state, self.is_closing = False.
- initialize(ctx): call register_talib_indicator_all_outputs(ctx, INDICATOR_NAME); reset prev_* and is_closing.
- on_bar(ctx, bar) MUST follow this order exactly:
  1. If ctx.position_size == 0: self.is_closing = False
  2. If ctx.get_open_orders(): return
  3. If not bar.get("is_new_bar", True): return
  4. Get indicator value with ctx.get_indicator(INDICATOR_NAME, period=...). Check math.isfinite(value); if not, return.
  5. If prev_value is None or not finite: self.prev_value = value; return
  6. Closing: if ctx.position_size > 0 and not self.is_closing and exit condition: self.is_closing = True; ctx.close_position(...); self.prev_value = value; return. Same for short (position_size < 0).
  7. Entry: only if ctx.position_size == 0: ctx.enter_long(...) or ctx.enter_short(...)
  8. At end: self.prev_value = value
- Use TA-Lib builtin name (e.g. RSI, MACD) in INDICATOR_NAME and in indicator_config. No other files or the template file must be modified; emit only this one strategy file.
- Code must be valid Python, no placeholders, no comments like "your code here".
"""


def build_system_prompt() -> str:
    return STRATEGY_SYSTEM_PROMPT
