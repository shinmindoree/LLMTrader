"""Post-process generated strategy code to ensure runtime correctness.

This module provides a safety net that fixes common issues in generated
strategy code (from DSL, LLM Coder, or Repair paths) before it reaches
the user or the backtest engine.

Key fix: LLM-generated code often references OHLCV variables (close, high,
low, volume) as bare names without defining them as locals. This module
detects such references and injects the necessary bindings.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Standard OHLCV binding lines to inject into on_bar.
# Order matters: close/price must come first (others reference close as fallback).
_OHLCV_BINDINGS: list[tuple[str, str]] = [
    ("close", "close = ctx.current_price"),
    ("price", "price = ctx.current_price"),
    ("open_", 'open_ = float(bar.get("open", close))'),
    ("high", 'high = float(bar.get("high", close))'),
    ("low", 'low = float(bar.get("low", close))'),
    ("volume", 'volume = float(bar.get("volume", 0))'),
]

# Marker to detect already-injected bindings
_OHLCV_MARKER = "close = ctx.current_price"


def ensure_ohlcv_bindings(code: str) -> str:
    """Inject OHLCV price variable bindings into on_bar if needed.

    This is idempotent — if bindings already exist, returns code unchanged.
    Works on any strategy code regardless of how it was generated.

    Returns the (possibly modified) code string.
    """
    if not code or not code.strip():
        return code

    # Fast path: already has bindings
    if _OHLCV_MARKER in code:
        return code

    # Parse AST to check which OHLCV names are used but not defined
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code  # can't parse; return as-is

    on_bar = _find_on_bar(tree)
    if on_bar is None:
        return code

    needed = _find_missing_ohlcv(on_bar)
    if not needed:
        return code

    # If 'close' is needed by other bindings (open_, high, low reference close as fallback),
    # always include it even if it's not directly referenced
    if needed - {"close", "price"}:
        needed.add("close")

    # Build injection block
    bindings_to_inject = [
        line for name, line in _OHLCV_BINDINGS if name in needed
    ]
    if not bindings_to_inject:
        return code

    # Find the right insertion point in the source
    lines = code.split("\n")
    insert_idx = _find_insertion_point(lines, on_bar)
    if insert_idx is None:
        return code

    # Detect indentation from surrounding code
    indent = _detect_indent(lines, on_bar)

    # Build injection text
    injection_lines = [f"{indent}# OHLCV price variables"]
    injection_lines.extend(f"{indent}{b}" for b in bindings_to_inject)
    injection_lines.append("")  # blank line separator

    # Insert
    for i, inj_line in enumerate(injection_lines):
        lines.insert(insert_idx + i, inj_line)

    result = "\n".join(lines)
    logger.info(
        "Injected %d OHLCV bindings into on_bar: %s",
        len(bindings_to_inject),
        ", ".join(sorted(needed)),
    )
    return result


def _find_on_bar(tree: ast.Module) -> ast.FunctionDef | None:
    """Find the on_bar method inside a Strategy class."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Strategy"):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "on_bar":
                    return item
    return None


def _find_missing_ohlcv(on_bar: ast.FunctionDef) -> set[str]:
    """Return set of OHLCV names that are used in on_bar but never assigned."""
    ohlcv_names = {name for name, _ in _OHLCV_BINDINGS}

    used: set[str] = set()
    assigned: set[str] = set()

    for node in ast.walk(on_bar):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                assigned.add(node.id)

    # Also count augmented assignment targets and for-loop targets
    for node in ast.walk(on_bar):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)

    return ohlcv_names & used - assigned


def _find_insertion_point(lines: list[str], on_bar: ast.FunctionDef) -> int | None:
    """Find the best 0-indexed line to insert OHLCV bindings.

    Strategy: insert after the guard block (is_new_bar / get_open_orders),
    before the first indicator read or logic.
    """
    body_start = on_bar.body[0].lineno - 1  # 0-indexed
    body_end = (on_bar.end_lineno or on_bar.body[-1].end_lineno or on_bar.body[-1].lineno)

    # Strategy 1: Look for "# Read indicators" comment
    for i in range(body_start, min(body_end, len(lines))):
        stripped = lines[i].strip()
        if stripped == "# Read indicators":
            return i

    # Strategy 2: Look for first ctx.get_indicator call
    for i in range(body_start, min(body_end, len(lines))):
        if "ctx.get_indicator" in lines[i]:
            # Insert before this line, but after any preceding blank line
            return i

    # Strategy 3: Find end of guard block (last return statement in a guard if)
    last_guard_end = body_start
    for stmt in on_bar.body:
        if isinstance(stmt, ast.If):
            # Check if this is a guard (body contains return)
            is_guard = any(isinstance(s, ast.Return) for s in stmt.body)
            if is_guard:
                last_guard_end = (stmt.end_lineno or stmt.lineno)
                continue
        # First non-guard, non-comment statement
        if not isinstance(stmt, ast.Expr):
            break
        # Could be a string expression (docstring)
        break

    # Insert after the last guard, skipping any blank lines
    insert_at = last_guard_end
    while insert_at < min(body_end, len(lines)) and not lines[insert_at].strip():
        insert_at += 1

    return insert_at


def _detect_indent(lines: list[str], on_bar: ast.FunctionDef) -> str:
    """Detect the indentation used in on_bar body."""
    if on_bar.body:
        first_line = on_bar.body[0].lineno - 1  # 0-indexed
        if first_line < len(lines):
            line = lines[first_line]
            return line[: len(line) - len(line.lstrip())]
    return "        "  # default 8-space indent
