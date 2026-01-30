"""System prompt for strategy code generation (indicator-strategy rules + template)."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "indicator_strategy_template.py"
_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "indicator-strategy" / "SKILL.md"

STRATEGY_SYSTEM_PROMPT_FALLBACK = """You generate a single Python file for a trading strategy. The file must be loadable by a runner that discovers a class whose name ends with "Strategy".

Rules:
- Output ONLY the contents of one new file: scripts/strategies/{name}_strategy.py (file path is logical; emit only the code, no markdown code fences).
- File name: snake_case, suffix _strategy.py. Class name: PascalCase ending with "Strategy" (e.g. RsiOversoldBounceLongStrategy).
- Imports: from strategy.base import Strategy; from strategy.context import StrategyContext.
- Include these helpers inside the file (do not import from template): _last_non_nan, register_talib_indicator_all_outputs, and if needed crossed_above, crossed_below. Copy them EXACTLY from the reference template below.
- Class: inherit Strategy; __init__ must call super().__init__(); set self.params (dict), self.indicator_config (dict), prev_* state, self.is_closing = False.
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
- Use TA-Lib builtin name (e.g. RSI, MACD) in INDICATOR_NAME and in indicator_config. Emit only this one strategy file.
- Code must be valid Python, no placeholders, no comments like "your code here". Do not wrap output in markdown code blocks.
"""


def _load_template_and_skill() -> tuple[str | None, str | None]:
    template = None
    skill = None
    if _TEMPLATE_PATH.exists():
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if _SKILL_PATH.exists():
        skill = _SKILL_PATH.read_text(encoding="utf-8")
    return template, skill


def build_system_prompt() -> str:
    template, skill = _load_template_and_skill()
    if template and skill:
        return (
            "You generate a single Python file for a trading strategy. The runner discovers a class whose name ends with \"Strategy\".\n\n"
            "## Rules (from indicator-strategy skill)\n\n"
            "- Output ONLY the raw Python code for one file. Do NOT wrap in markdown code fences (no ```).\n"
            "- File: scripts/strategies/{name}_strategy.py. Class: PascalCase ending with \"Strategy\".\n"
            "- Imports: from strategy.base import Strategy; from strategy.context import StrategyContext.\n"
            "- You MUST copy the helper functions _last_non_nan, register_talib_indicator_all_outputs, crossed_above, crossed_below EXACTLY from the reference template below into your output. Do not simplify or rewrite them.\n"
            "- Class: inherit Strategy; __init__ must call super().__init__(); set self.params, self.indicator_config, prev_* state, self.is_closing = False.\n"
            "- initialize(ctx): call register_talib_indicator_all_outputs(ctx, INDICATOR_NAME); reset prev_* and is_closing.\n"
            "- on_bar(ctx, bar) order: 1) position_size==0 -> is_closing=False 2) get_open_orders() -> return 3) not bar.get(\"is_new_bar\",True) -> return 4) get_indicator, isfinite check 5) prev init 6) closing logic 7) entry logic 8) prev update.\n"
            "- TA-Lib builtin: INDICATOR_NAME and indicator_config use names like RSI, MACD. Emit only this one file, valid Python only.\n\n"
            "## Reference template (copy helpers exactly, then implement your strategy class)\n\n"
            + template
            + "\n"
        )
    return STRATEGY_SYSTEM_PROMPT_FALLBACK
