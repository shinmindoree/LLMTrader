"""Agent tools for strategy generation.

Provides file-system and execution tools that the LLM agent can invoke
during the strategy generation loop. Each tool function returns a string
result that gets fed back to the LLM as a tool response.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STRATEGIES_DIR = _REPO_ROOT / "scripts" / "strategies"
_SRC_DIR = _REPO_ROOT / "src"

# Allowed read paths (relative to repo root) — security boundary
_ALLOWED_READ_PREFIXES = (
    "src/strategy/",
    "src/backtest/",
    "scripts/strategies/",
    "scripts/AGENTS.md",
    "indicator_strategy_template.py",
    ".cursor/skills/indicator-strategy/",
    ".cursor/skills/strategy-verify/",
)

# Max file content to return (chars) to avoid context explosion
_MAX_FILE_CONTENT = 15_000
_MAX_SEARCH_RESULTS = 10
_MAX_BACKTEST_OUTPUT = 3_000


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "read_file",
        "description": (
            "Read the contents of a file from the codebase. "
            "Use this to read strategy interface files (context.py, base.py, AGENTS.md), "
            "the template, SKILL.md rules, or existing strategy examples."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path relative to project root. Examples: "
                        "'src/strategy/context.py', 'indicator_strategy_template.py', "
                        "'.cursor/skills/indicator-strategy/SKILL.md', "
                        "'scripts/strategies/rsi_long_short_strategy.py'"
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "search_code",
        "description": (
            "Search for code patterns across the strategy codebase. "
            "Use this to find how specific patterns are implemented "
            "(e.g., 'ATR', 'add_to_long', 'register_indicator', 'VWAP')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text pattern to search for in strategy files.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "list_strategies",
        "description": (
            "List all existing strategy files in scripts/strategies/. "
            "Returns filenames so you can then read relevant ones."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "type": "function",
        "name": "write_strategy",
        "description": (
            "Write the generated strategy code to a file and verify it loads correctly. "
            "Returns 'OK: ClassName' on success or the error message on failure. "
            "Call this when you have complete strategy code ready."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Filename (not full path) for the strategy, e.g. 'rsi_atr_risk_reward_strategy.py'. "
                        "Must end with '_strategy.py'."
                    ),
                },
                "code": {
                    "type": "string",
                    "description": "Complete Python strategy code.",
                },
            },
            "required": ["filename", "code"],
        },
    },
    {
        "type": "function",
        "name": "run_backtest",
        "description": (
            "Run a short 3-day backtest to verify the strategy works at runtime. "
            "Returns 'BACKTEST_OK' on success or the error output on failure. "
            "Call this after write_strategy succeeds to confirm runtime correctness."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Strategy filename in scripts/strategies/.",
                },
            },
            "required": ["filename"],
        },
    },
    {
        "type": "function",
        "name": "done",
        "description": (
            "Signal that strategy generation is complete. "
            "Call this after the strategy passes both write_strategy and run_backtest."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Final strategy filename.",
                },
                "code": {
                    "type": "string",
                    "description": "Final verified strategy code.",
                },
                "summary": {
                    "type": "string",
                    "description": "One-line Korean summary of the strategy.",
                },
            },
            "required": ["filename", "code"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _is_path_allowed(rel_path: str) -> bool:
    """Check if a relative path is within the allowed read boundary."""
    normalized = rel_path.replace("\\", "/").lstrip("/")
    return any(normalized.startswith(prefix) for prefix in _ALLOWED_READ_PREFIXES)


def tool_read_file(path: str) -> str:
    """Read a file from the allowed codebase paths."""
    normalized = path.replace("\\", "/").lstrip("/")

    if not _is_path_allowed(normalized):
        return f"ERROR: Path '{normalized}' is outside the allowed read boundary. Allowed: {', '.join(_ALLOWED_READ_PREFIXES)}"

    full_path = _REPO_ROOT / normalized
    if not full_path.is_file():
        return f"ERROR: File not found: {normalized}"

    try:
        content = full_path.read_text(encoding="utf-8")
        if len(content) > _MAX_FILE_CONTENT:
            content = content[:_MAX_FILE_CONTENT] + f"\n\n... [truncated at {_MAX_FILE_CONTENT} chars]"
        return content
    except Exception as e:
        return f"ERROR: Failed to read {normalized}: {e}"


def tool_search_code(query: str) -> str:
    """Search for a pattern across strategy-related files."""
    if not query or not query.strip():
        return "ERROR: Empty search query."

    query_lower = query.strip().lower()
    results: list[str] = []
    count = 0

    search_dirs = [
        _STRATEGIES_DIR,
        _SRC_DIR / "strategy",
    ]
    search_files = [
        _REPO_ROOT / "indicator_strategy_template.py",
    ]

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for py_file in sorted(search_dir.rglob("*.py")):
            if count >= _MAX_SEARCH_RESULTS:
                break
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if query_lower in line.lower():
                    rel = py_file.relative_to(_REPO_ROOT)
                    results.append(f"{rel}:{i}: {line.strip()}")
                    count += 1
                    if count >= _MAX_SEARCH_RESULTS:
                        break

    for single_file in search_files:
        if count >= _MAX_SEARCH_RESULTS:
            break
        if not single_file.is_file():
            continue
        try:
            content = single_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if query_lower in line.lower():
                rel = single_file.relative_to(_REPO_ROOT)
                results.append(f"{rel}:{i}: {line.strip()}")
                count += 1
                if count >= _MAX_SEARCH_RESULTS:
                    break

    if not results:
        return f"No matches found for '{query}'."
    return "\n".join(results)


def tool_list_strategies() -> str:
    """List all strategy files."""
    if not _STRATEGIES_DIR.is_dir():
        return "ERROR: scripts/strategies/ directory not found."

    files = sorted(p.name for p in _STRATEGIES_DIR.glob("*_strategy.py"))
    if not files:
        return "No strategy files found."
    return "\n".join(files)


def tool_write_strategy(filename: str, code: str) -> str:
    """Write strategy code and verify it loads + instantiates correctly."""
    if not filename.endswith("_strategy.py"):
        return "ERROR: Filename must end with '_strategy.py'."

    # Security: validate code with AST before writing
    from llm.strategy_validator import SecurityError, validate_strategy_code

    try:
        validate_strategy_code(code)
    except (SecurityError, SyntaxError) as e:
        return f"SECURITY_ERROR: {e}"

    # Write to a temp file for load testing (don't overwrite real file yet)
    tmp_path = _STRATEGIES_DIR / f".tmp_{filename}"
    try:
        tmp_path.write_text(code, encoding="utf-8")

        # Verify load + instantiation
        result = subprocess.run(
            [
                "uv", "run", "python", "-c",
                (
                    "import sys; sys.path.insert(0, 'src');"
                    "from strategy.base import Strategy;"
                    "import importlib.util;"
                    f"spec = importlib.util.spec_from_file_location('strat', r'{tmp_path}');"
                    "mod = importlib.util.module_from_spec(spec);"
                    "spec.loader.exec_module(mod);"
                    "cls = next((getattr(mod, n) for n in dir(mod) "
                    "if n.endswith('Strategy') and n != 'Strategy'), None);"
                    "assert cls is not None and issubclass(cls, Strategy), "
                    f"'No Strategy subclass found';"
                    "inst = cls();"
                    "print('OK:', cls.__name__)"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_REPO_ROOT),
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Extract the most useful error lines
            error_lines = [
                l for l in stderr.splitlines()
                if "Error" in l or "assert" in l.lower() or "Traceback" in l
            ]
            error_msg = "\n".join(error_lines[-5:]) if error_lines else stderr[-500:]
            return f"LOAD_ERROR:\n{error_msg}"

        stdout = result.stdout.strip()
        if stdout.startswith("OK:"):
            # Load succeeded — write to actual file
            target_path = _STRATEGIES_DIR / filename
            target_path.write_text(code, encoding="utf-8")
            return stdout

        return f"UNEXPECTED_OUTPUT: {stdout}"

    except subprocess.TimeoutExpired:
        return "LOAD_ERROR: Load verification timed out (30s)."
    except Exception as e:
        return f"LOAD_ERROR: {e}"
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def tool_run_backtest(filename: str) -> str:
    """Run a short 3-day backtest to verify runtime correctness."""
    strategy_path = _STRATEGIES_DIR / filename
    if not strategy_path.is_file():
        return f"ERROR: Strategy file not found: {filename}"

    try:
        result = subprocess.run(
            [
                "uv", "run", "python", "scripts/run_backtest.py",
                str(strategy_path),
                "--symbol", "BTCUSDT",
                "--candle-interval", "1h",
                "--start-date", "2024-06-01",
                "--end-date", "2024-06-03",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_REPO_ROOT),
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            error_lines = [
                l for l in stderr.splitlines()
                if any(kw in l for kw in ("Error", "Traceback", "assert", "Exception"))
            ]
            error_msg = "\n".join(error_lines[-8:]) if error_lines else stderr[-_MAX_BACKTEST_OUTPUT:]
            return f"BACKTEST_ERROR:\n{error_msg}"

        # Extract key result info from stdout
        stdout = result.stdout.strip()
        # Return last N chars to stay within budget
        if len(stdout) > _MAX_BACKTEST_OUTPUT:
            stdout = stdout[-_MAX_BACKTEST_OUTPUT:]
        return f"BACKTEST_OK\n{stdout}"

    except subprocess.TimeoutExpired:
        return "BACKTEST_ERROR: Backtest timed out (120s)."
    except Exception as e:
        return f"BACKTEST_ERROR: {e}"


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

_TOOL_MAP = {
    "read_file": lambda args: tool_read_file(args["path"]),
    "search_code": lambda args: tool_search_code(args["query"]),
    "list_strategies": lambda _args: tool_list_strategies(),
    "write_strategy": lambda args: tool_write_strategy(args["filename"], args["code"]),
    "run_backtest": lambda args: tool_run_backtest(args["filename"]),
}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool by name, returning the result string."""
    handler = _TOOL_MAP.get(name)
    if handler is None:
        return f"ERROR: Unknown tool '{name}'."
    try:
        return handler(arguments)
    except Exception as e:
        logger.exception("Tool '%s' failed: %s", name, e)
        return f"ERROR: Tool '{name}' raised: {e}"
