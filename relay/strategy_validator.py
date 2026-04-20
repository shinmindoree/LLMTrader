"""AST-based security validator for LLM-generated strategy code.

This module performs structural analysis of Python code using the Abstract
Syntax Tree (AST) to detect and block dangerous constructs BEFORE execution.
It serves as a hardware-level pre-execution gate that prevents hallucinated
or malicious code from reaching the runtime environment.

Blocked categories:
  - Forbidden imports (os, sys, subprocess, shutil, socket, ctypes, …)
  - Dangerous built-in calls (open, exec, eval, compile, __import__, …)
  - Infinite loops (while True, while 1)
  - Attribute access to dunder internals (__subclasses__, __globals__, …)
"""

from __future__ import annotations

import ast
import logging
from typing import Sequence

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when generated code contains a forbidden construct."""


# ---------------------------------------------------------------------------
# Configuration: blocklists
# ---------------------------------------------------------------------------

# Top-level module names that must never be imported.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "socket",
        "http",
        "urllib",
        "requests",
        "ctypes",
        "signal",
        "multiprocessing",
        "threading",
        "pickle",
        "shelve",
        "marshal",
        "importlib",
        "pathlib",
        "glob",
        "tempfile",
        "io",
        "code",
        "codeop",
        "compileall",
        "webbrowser",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "builtins",
        "gc",
        "inspect",
        "runpy",
    }
)

# Built-in function / name calls that are dangerous.
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "breakpoint",
        "exit",
        "quit",
        "input",
        "memoryview",
        "type",  # type() with 3 args can create classes dynamically
    }
)

# Dunder attribute names that can be used to escape sandboxes.
FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__globals__",
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        "__code__",
        "__class__",
        "__bases__",
        "__mro__",
        "__dict__",
        "__module__",
        "__qualname__",
    }
)


# ---------------------------------------------------------------------------
# AST Visitor
# ---------------------------------------------------------------------------


class _SecurityVisitor(ast.NodeVisitor):
    """Walk the AST and collect all security violations."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    # -- Import statements ---------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in FORBIDDEN_MODULES:
                self.violations.append(
                    f"line {node.lineno}: forbidden import '{alias.name}'"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top = node.module.split(".")[0]
            if top in FORBIDDEN_MODULES:
                self.violations.append(
                    f"line {node.lineno}: forbidden import from '{node.module}'"
                )
        self.generic_visit(node)

    # -- Dangerous function calls --------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        name = _resolve_call_name(node)
        if name in FORBIDDEN_CALLS:
            self.violations.append(
                f"line {node.lineno}: forbidden call '{name}()'"
            )
        self.generic_visit(node)

    # -- Dangerous attribute access ------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRS:
            self.violations.append(
                f"line {node.lineno}: forbidden attribute access '{node.attr}'"
            )
        self.generic_visit(node)

    # -- Infinite loops ------------------------------------------------------

    def visit_While(self, node: ast.While) -> None:
        if _is_constant_true(node.test):
            self.violations.append(
                f"line {node.lineno}: potentially infinite loop 'while True'"
            )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_call_name(node: ast.Call) -> str:
    """Return the simple name of a Call node (e.g. 'open', 'eval')."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_constant_true(node: ast.expr) -> bool:
    """Check if an AST expression is a constant truthy literal (True, 1, …)."""
    if isinstance(node, ast.Constant):
        return bool(node.value) and node.value is not None
    # Python 3.7 compat (ast.NameConstant removed in 3.12)
    if isinstance(node, ast.Name) and node.id == "True":
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_strategy_code(code: str) -> str:
    """Parse and validate LLM-generated strategy code via AST analysis.

    Args:
        code: Raw Python source string returned by the LLM.

    Returns:
        The original code string, unmodified, if it passes all checks.

    Raises:
        SecurityError: If any forbidden construct is detected.
        SyntaxError: If the code cannot be parsed as valid Python.
    """
    if not code or not code.strip():
        raise SecurityError("Empty code received from LLM")

    # Phase 1: Parse into AST (also catches syntax errors early).
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SyntaxError(
            f"LLM-generated code has a syntax error at line {exc.lineno}: {exc.msg}"
        ) from exc

    # Phase 2: Walk the AST and collect violations.
    visitor = _SecurityVisitor()
    visitor.visit(tree)

    if visitor.violations:
        details = "; ".join(visitor.violations)
        logger.warning("Strategy code blocked: %s", details)
        raise SecurityError(
            f"LLM-generated code contains {len(visitor.violations)} "
            f"security violation(s): {details}"
        )

    logger.info("Strategy code passed AST security validation")
    return code
