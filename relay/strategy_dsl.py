"""Simple DSL schema for deterministic strategy code generation.

When the Planner produces a DSL-compatible spec (no custom_indicator), the
strategy code can be generated via template expansion — no LLM Coder needed.
Complex strategies with custom indicators fall back to the LLM Coder path.
"""

from __future__ import annotations

import ast
import re
import textwrap
from typing import Any

from pydantic import BaseModel, field_validator, model_validator


class IndicatorSpec(BaseModel):
    name: str  # e.g. "RSI", "EMA", "MACD", "STOCH", "BBANDS"
    params: dict[str, Any] = {}  # e.g. {"period": 14}
    alias: str = ""  # e.g. "fast_ema" — used as variable name

    @field_validator("alias", mode="before")
    @classmethod
    def default_alias(cls, v: str, info: Any) -> str:
        if v:
            return v
        name = info.data.get("name", "ind")
        return name.lower()


# AST node types allowed inside condition_expr (whitelist)
_SAFE_EXPR_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Call, ast.Constant, ast.Name, ast.Attribute, ast.Subscript,
    ast.Load, ast.And, ast.Or, ast.Not, ast.Add, ast.Sub, ast.Mult,
    ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.IfExp, ast.Tuple, ast.List, ast.Index, ast.Slice,
    ast.USub, ast.UAdd,
)

# Names that must never appear in condition_expr
_DANGEROUS_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "open", "breakpoint",
    "globals", "locals", "vars", "dir", "__builtins__",
    "os", "sys", "subprocess", "importlib", "shutil", "pathlib",
})


def validate_condition_expr(expr: str) -> str | None:
    """Validate that a condition expression is a safe single expression.

    Returns None if valid, or an error message string.
    """
    if not expr or not expr.strip():
        return "Empty condition expression"
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        return f"Invalid expression syntax: {e.msg}"
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_EXPR_NODES):
            return f"Disallowed construct in condition_expr: {type(node).__name__}"
        if isinstance(node, ast.Name) and node.id in _DANGEROUS_NAMES:
            return f"Disallowed name in condition_expr: {node.id}"
        if isinstance(node, ast.Attribute) and node.attr in _DANGEROUS_NAMES:
            return f"Disallowed attribute in condition_expr: {node.attr}"
    return None


class ConditionSpec(BaseModel):
    condition_expr: str  # Python expression, e.g. "crossed_above(prev_rsi, rsi, 30)"
    reason_template: str = ""  # e.g. "RSI crossed above {level}"

    @field_validator("condition_expr")
    @classmethod
    def check_condition_expr(cls, v: str) -> str:
        error = validate_condition_expr(v)
        if error:
            raise ValueError(error)
        return v


class RiskSpec(BaseModel):
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None


class ParamSpec(BaseModel):
    default: int | float | bool | str
    type: str = "number"  # "integer" | "number" | "boolean" | "string"
    label: str = ""
    description: str = ""
    group: str = "일반 (General)"
    min: float | None = None
    max: float | None = None


class StrategyDSL(BaseModel):
    strategy_name: str  # PascalCase ending in "Strategy"
    direction: str = "long_only"  # "long_only" | "short_only" | "long_short"
    indicators: list[IndicatorSpec] = []
    state_vars: list[str] = []  # e.g. ["prev_rsi", "prev_macd"]
    entry_long: ConditionSpec | None = None
    exit_long: ConditionSpec | None = None
    entry_short: ConditionSpec | None = None
    exit_short: ConditionSpec | None = None
    risk: RiskSpec = RiskSpec()
    tunable_params: dict[str, ParamSpec] = {}
    run_on_tick: bool = False
    custom_indicator: str | None = None  # If set → LLM fallback

    @field_validator("strategy_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.endswith("Strategy"):
            v += "Strategy"
        if not re.match(r"^[A-Z][A-Za-z0-9]+Strategy$", v):
            raise ValueError(f"Invalid strategy name: {v}")
        return v

    @model_validator(mode="after")
    def validate_unique_aliases(self) -> "StrategyDSL":
        aliases = [ind.alias for ind in self.indicators]
        seen: set[str] = set()
        for alias in aliases:
            if alias in seen:
                raise ValueError(f"Duplicate indicator alias: {alias!r}")
            seen.add(alias)
        return self

    def needs_llm_fallback(self) -> bool:
        return bool(self.custom_indicator)


def generate_strategy_code(dsl: StrategyDSL) -> str:
    """Generate complete Python strategy code from a DSL spec."""
    lines: list[str] = []

    # Imports
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("import math")
    lines.append("from typing import Any")
    lines.append("")
    lines.append("from strategy.base import Strategy")
    lines.append("from strategy.context import StrategyContext")

    # Check if MACD/multi-output indicator is used
    multi_output_indicators = {"MACD", "STOCH", "STOCHF", "STOCHRSI", "BBANDS", "AROON"}
    needs_register = any(ind.name.upper() in multi_output_indicators for ind in dsl.indicators)
    if needs_register:
        lines.append("from indicator_strategy_template import register_talib_indicator_all_outputs")

    lines.append("")
    lines.append("")

    # Helper functions
    lines.append("def crossed_above(prev: float, current: float, level: float) -> bool:")
    lines.append('    """prev < level <= current"""')
    lines.append("    return prev < level <= current")
    lines.append("")
    lines.append("")
    lines.append("def crossed_below(prev: float, current: float, level: float) -> bool:")
    lines.append('    """current <= level < prev"""')
    lines.append("    return current <= level < prev")
    lines.append("")
    lines.append("")

    # STRATEGY_PARAMS
    lines.append("STRATEGY_PARAMS: dict[str, Any] = {")
    for pname, pspec in dsl.tunable_params.items():
        lines.append(f"    {pname!r}: {pspec.default!r},")
    lines.append("}")
    lines.append("")

    # STRATEGY_PARAM_SCHEMA
    if dsl.tunable_params:
        lines.append("STRATEGY_PARAM_SCHEMA: dict[str, Any] = {")
        for pname, pspec in dsl.tunable_params.items():
            schema_parts = [f'"type": {pspec.type!r}']
            if pspec.label:
                schema_parts.append(f'"label": {pspec.label!r}')
            if pspec.description:
                schema_parts.append(f'"description": {pspec.description!r}')
            if pspec.group:
                schema_parts.append(f'"group": {pspec.group!r}')
            if pspec.min is not None:
                schema_parts.append(f'"min": {pspec.min!r}')
            if pspec.max is not None:
                schema_parts.append(f'"max": {pspec.max!r}')
            lines.append(f"    {pname!r}: {{{', '.join(schema_parts)}}},")
        lines.append("}")
        lines.append("")

    lines.append("")

    # Class definition
    lines.append(f"class {dsl.strategy_name}(Strategy):")
    lines.append(f'    """DSL-generated strategy: {dsl.strategy_name}."""')
    lines.append("")

    # __init__
    lines.append("    def __init__(self, **kwargs: Any) -> None:")
    lines.append("        super().__init__()")
    lines.append("        p = {**STRATEGY_PARAMS, **kwargs}")

    # Read params
    for pname, pspec in dsl.tunable_params.items():
        cast = "int" if pspec.type == "integer" else "float" if pspec.type == "number" else "bool" if pspec.type == "boolean" else "str"
        lines.append(f"        self.{pname} = {cast}(p[{pname!r}])")

    # State vars
    for sv in dsl.state_vars:
        lines.append(f"        self.{sv}: float | None = None")
    lines.append("        self.is_closing: bool = False")

    if dsl.run_on_tick:
        lines.append("        self.run_on_tick = True")

    # indicator_config
    if dsl.indicators:
        lines.append("        self.indicator_config = {")
        for ind in dsl.indicators:
            param_str = ", ".join(f'"{k}": self.{_param_to_attr(ind.alias, k)}' for k in ind.params if _find_param(dsl.tunable_params, ind.alias, k))
            if not param_str:
                param_str = ", ".join(f'"{k}": {v!r}' for k, v in ind.params.items())
            lines.append(f'            "{ind.name}": {{{param_str}}},')
        lines.append("        }")
    lines.append("")

    # initialize
    lines.append("    def initialize(self, ctx: StrategyContext) -> None:")
    for ind in dsl.indicators:
        if ind.name.upper() in multi_output_indicators:
            lines.append(f'        register_talib_indicator_all_outputs(ctx, "{ind.name}")')
    for sv in dsl.state_vars:
        lines.append(f"        self.{sv} = None")
    lines.append("        self.is_closing = False")
    lines.append("")

    # on_bar
    lines.append("    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:")
    lines.append("        # Reset closing flag")
    lines.append("        if ctx.position_size == 0:")
    lines.append("            self.is_closing = False")
    lines.append("")
    lines.append("        # Guard: pending orders")
    lines.append("        if ctx.get_open_orders():")
    lines.append("            return")

    if not dsl.run_on_tick:
        lines.append("")
        lines.append("        # Guard: new bar only")
        lines.append('        if not bool(bar.get("is_new_bar", True)):')
        lines.append("            return")

    lines.append("")

    # OHLCV price variables (available in condition_expr)
    lines.append("        # Price variables")
    lines.append("        close = ctx.current_price")
    lines.append("        price = ctx.current_price")
    lines.append('        open_ = float(bar.get("open", close))')
    lines.append('        high = float(bar.get("high", close))')
    lines.append('        low = float(bar.get("low", close))')
    lines.append('        volume = float(bar.get("volume", 0))')
    lines.append("")

    # Read indicators
    lines.append("        # Read indicators")
    for ind in dsl.indicators:
        alias = ind.alias
        param_kwargs = ", ".join(f"{k}=self.{_param_to_attr(alias, k)}" if _find_param(dsl.tunable_params, alias, k) else f"{k}={v!r}" for k, v in ind.params.items())
        if ind.name.upper() in multi_output_indicators:
            lines.append(f'        {alias}_data = ctx.get_indicator("{ind.name}", {param_kwargs})')
            lines.append(f"        if not isinstance({alias}_data, dict):")
            lines.append("            return")
        else:
            lines.append(f'        {alias} = float(ctx.get_indicator("{ind.name}", {param_kwargs}))')
            lines.append(f"        if not math.isfinite({alias}):")
            lines.append("            return")
    lines.append("")

    # Build mapping from state_var → current expression
    prev_checks = [sv for sv in dsl.state_vars if sv.startswith("prev_")]
    multi_output_key_map = _build_multi_output_key_map(dsl.indicators, multi_output_indicators)
    if prev_checks:
        lines.append("        # Initialize prev values")
        cond = " or ".join(f"self.{sv} is None" for sv in prev_checks)
        lines.append(f"        if {cond}:")
        for sv in prev_checks:
            current_expr = _resolve_prev_var(sv, multi_output_key_map)
            lines.append(f"            self.{sv} = {current_expr}")
        lines.append("            return")
        lines.append("")

    # Risk management: stop-loss / take-profit
    has_risk = dsl.risk.stop_loss_pct is not None or dsl.risk.take_profit_pct is not None
    if has_risk:
        lines.append("        # Risk management: stop-loss / take-profit")
        lines.append("        if ctx.position_size != 0:")
        lines.append("            _entry = ctx.position_entry_price")
        lines.append("            if _entry and _entry > 0:")
        lines.append("                _pnl_pct = ((close - _entry) / _entry * 100) if ctx.position_size > 0 else ((_entry - close) / _entry * 100)")
        if dsl.risk.stop_loss_pct is not None:
            lines.append(f"                if _pnl_pct <= -{abs(dsl.risk.stop_loss_pct)!r}:")
            lines.append("                    self.is_closing = True")
            lines.append(f'                    ctx.close_position(exit_reason="Stop-loss {abs(dsl.risk.stop_loss_pct)}% hit")')
            lines.append("                    self.prev_value = None  # type: ignore[assignment]")
            lines.append("                    return")
        if dsl.risk.take_profit_pct is not None:
            lines.append(f"                if _pnl_pct >= {abs(dsl.risk.take_profit_pct)!r}:")
            lines.append("                    self.is_closing = True")
            lines.append(f'                    ctx.close_position(exit_reason="Take-profit {abs(dsl.risk.take_profit_pct)}% hit")')
            lines.append("                    self.prev_value = None  # type: ignore[assignment]")
            lines.append("                    return")
        lines.append("")

    # Exit logic (before entry to allow same-bar reversal)
    if dsl.exit_long and dsl.direction in ("long_only", "long_short"):
        lines.append("        # Long exit")
        lines.append("        if ctx.position_size > 0 and not self.is_closing:")
        reason = _escape_reason(dsl.exit_long.reason_template or "Long exit signal")
        lines.append(f"            if {dsl.exit_long.condition_expr}:")
        lines.append("                self.is_closing = True")
        lines.append(f'                ctx.close_position(exit_reason="{reason}")')
        lines.append("")

    if dsl.exit_short and dsl.direction in ("short_only", "long_short"):
        lines.append("        # Short exit")
        lines.append("        if ctx.position_size < 0 and not self.is_closing:")
        reason = _escape_reason(dsl.exit_short.reason_template or "Short exit signal")
        lines.append(f"            if {dsl.exit_short.condition_expr}:")
        lines.append("                self.is_closing = True")
        lines.append(f'                ctx.close_position(exit_reason="{reason}")')
        lines.append("")

    # Entry logic
    if dsl.entry_long and dsl.direction in ("long_only", "long_short"):
        lines.append("        # Long entry")
        lines.append("        if ctx.position_size == 0:")
        reason = _escape_reason(dsl.entry_long.reason_template or "Long entry signal")
        lines.append(f"            if {dsl.entry_long.condition_expr}:")
        lines.append(f'                ctx.enter_long(reason="{reason}")')
        lines.append("")

    if dsl.entry_short and dsl.direction in ("short_only", "long_short"):
        lines.append("        # Short entry")
        lines.append("        if ctx.position_size == 0:")
        reason = _escape_reason(dsl.entry_short.reason_template or "Short entry signal")
        lines.append(f"            if {dsl.entry_short.condition_expr}:")
        lines.append(f'                ctx.enter_short(reason="{reason}")')
        lines.append("")

    # Update prev values
    if prev_checks:
        lines.append("        # Update prev values")
        for sv in prev_checks:
            current_expr = _resolve_prev_var(sv, multi_output_key_map)
            lines.append(f"        self.{sv} = {current_expr}")

    return "\n".join(lines) + "\n"


def _param_to_attr(alias: str, key: str) -> str:
    """Convert indicator alias + param key to STRATEGY_PARAMS attribute name."""
    return f"{alias}_{key}"


def _find_param(tunable: dict[str, ParamSpec], alias: str, key: str) -> bool:
    """Check if a tunable param exists for this indicator param."""
    return _param_to_attr(alias, key) in tunable


def _escape_reason(reason: str) -> str:
    """Escape quotes in reason strings for safe embedding in generated code."""
    return reason.replace("\\", "\\\\").replace('"', '\\"')


# Known sub-key names for multi-output TA-Lib indicators
_MULTI_OUTPUT_KEYS: dict[str, list[str]] = {
    "MACD": ["macd", "macdsignal", "macdhist"],
    "STOCH": ["slowk", "slowd"],
    "STOCHF": ["fastk", "fastd"],
    "STOCHRSI": ["fastk", "fastd"],
    "BBANDS": ["upperband", "middleband", "lowerband"],
    "AROON": ["aroondown", "aroonup"],
}


def _build_multi_output_key_map(
    indicators: list[IndicatorSpec],
    multi_output_indicators: set[str],
) -> dict[str, str]:
    """Build mapping: flattened_name → expr for multi-output indicator sub-keys.

    E.g. for alias='bbands', name='BBANDS':
      'bbands_upperband' → 'bbands_data["upperband"]'
      'bbands_middleband' → 'bbands_data["middleband"]'
    """
    result: dict[str, str] = {}
    for ind in indicators:
        upper = ind.name.upper()
        if upper in multi_output_indicators and upper in _MULTI_OUTPUT_KEYS:
            for key in _MULTI_OUTPUT_KEYS[upper]:
                flat_name = f"{ind.alias}_{key}"
                result[flat_name] = f'{ind.alias}_data["{key}"]'
    return result


def _resolve_prev_var(state_var: str, multi_key_map: dict[str, str]) -> str:
    """Resolve a prev_ state variable to its current-value expression.

    For single-output indicators: prev_rsi → rsi
    For multi-output: prev_bbands_upperband → bbands_data["upperband"]
    For OHLCV: prev_close → close
    """
    current = state_var.removeprefix("prev_")
    if current in multi_key_map:
        return multi_key_map[current]
    return current


def parse_planner_dsl(planner_json: dict[str, Any]) -> StrategyDSL | None:
    """Try to parse planner JSON output as a StrategyDSL.

    Returns None if the planner output doesn't match DSL schema
    (e.g. missing required fields, or has custom_indicator).
    """
    try:
        return StrategyDSL.model_validate(planner_json)
    except Exception:
        return None
