"""Simple DSL schema for deterministic strategy code generation.

When the Planner produces a DSL-compatible spec (no custom_indicator), the
strategy code can be generated via template expansion — no LLM Coder needed.
Complex strategies with custom indicators fall back to the LLM Coder path.
"""

from __future__ import annotations

import re
import textwrap
from typing import Any

from pydantic import BaseModel, field_validator


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


class ConditionSpec(BaseModel):
    condition_expr: str  # Python expression, e.g. "crossed_above(prev_rsi, rsi, 30)"
    reason_template: str = ""  # e.g. "RSI crossed above {level}"


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

    # Check prev values initialized
    prev_checks = [sv for sv in dsl.state_vars if sv.startswith("prev_")]
    if prev_checks:
        lines.append("        # Initialize prev values")
        cond = " or ".join(f"self.{sv} is None" for sv in prev_checks)
        lines.append(f"        if {cond}:")
        for sv in prev_checks:
            # Derive current value from the state var name
            current = sv.replace("prev_", "")
            lines.append(f"            self.{sv} = {current}")
        lines.append("            return")
        lines.append("")

    # Exit logic (before entry to allow same-bar reversal)
    if dsl.exit_long and dsl.direction in ("long_only", "long_short"):
        lines.append("        # Long exit")
        lines.append("        if ctx.position_size > 0 and not self.is_closing:")
        reason = dsl.exit_long.reason_template or "Long exit signal"
        lines.append(f"            if {dsl.exit_long.condition_expr}:")
        lines.append("                self.is_closing = True")
        lines.append(f'                ctx.close_position(exit_reason="{reason}")')
        lines.append("")

    if dsl.exit_short and dsl.direction in ("short_only", "long_short"):
        lines.append("        # Short exit")
        lines.append("        if ctx.position_size < 0 and not self.is_closing:")
        reason = dsl.exit_short.reason_template or "Short exit signal"
        lines.append(f"            if {dsl.exit_short.condition_expr}:")
        lines.append("                self.is_closing = True")
        lines.append(f'                ctx.close_position(exit_reason="{reason}")')
        lines.append("")

    # Entry logic
    if dsl.entry_long and dsl.direction in ("long_only", "long_short"):
        lines.append("        # Long entry")
        lines.append("        if ctx.position_size == 0:")
        reason = dsl.entry_long.reason_template or "Long entry signal"
        lines.append(f"            if {dsl.entry_long.condition_expr}:")
        lines.append(f'                ctx.enter_long(reason="{reason}")')
        lines.append("")

    if dsl.entry_short and dsl.direction in ("short_only", "long_short"):
        lines.append("        # Short entry")
        lines.append("        if ctx.position_size == 0:")
        reason = dsl.entry_short.reason_template or "Short entry signal"
        lines.append(f"            if {dsl.entry_short.condition_expr}:")
        lines.append(f'                ctx.enter_short(reason="{reason}")')
        lines.append("")

    # Update prev values
    if prev_checks:
        lines.append("        # Update prev values")
        for sv in prev_checks:
            current = sv.replace("prev_", "")
            lines.append(f"        self.{sv} = {current}")

    return "\n".join(lines) + "\n"


def _param_to_attr(alias: str, key: str) -> str:
    """Convert indicator alias + param key to STRATEGY_PARAMS attribute name."""
    return f"{alias}_{key}"


def _find_param(tunable: dict[str, ParamSpec], alias: str, key: str) -> bool:
    """Check if a tunable param exists for this indicator param."""
    return _param_to_attr(alias, key) in tunable


def parse_planner_dsl(planner_json: dict[str, Any]) -> StrategyDSL | None:
    """Try to parse planner JSON output as a StrategyDSL.

    Returns None if the planner output doesn't match DSL schema
    (e.g. missing required fields, or has custom_indicator).
    """
    try:
        return StrategyDSL.model_validate(planner_json)
    except Exception:
        return None
