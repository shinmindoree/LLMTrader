"""STRATEGY_PARAMS / STRATEGY_PARAM_SCHEMA 추출 및 AST 기반 안전 패치."""

from __future__ import annotations

import ast
from typing import Any


class StrategyParamsError(ValueError):
    """추출/적용 실패."""


def _eval_dict_literal(node: ast.expr) -> dict[str, Any]:
    try:
        dumped = ast.unparse(node)
        out = ast.literal_eval(dumped)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise StrategyParamsError(f"dict literal could not be evaluated: {exc}") from exc
    if not isinstance(out, dict):
        raise StrategyParamsError("value is not a dict")
    for k in out:
        if not isinstance(k, str):
            raise StrategyParamsError("dict keys must be strings")
    return out


def _value_to_ast(value: Any) -> ast.expr:
    if isinstance(value, bool):
        return ast.Constant(value=value)
    if isinstance(value, int) and not isinstance(value, bool):
        return ast.Constant(value=value)
    if isinstance(value, float):
        return ast.Constant(value=value)
    if isinstance(value, str):
        return ast.Constant(value=value)
    raise StrategyParamsError(f"unsupported value type for STRATEGY_PARAMS: {type(value)!r}")


def _infer_schema_field(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean", "label": key}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer", "label": key}
    if isinstance(value, float):
        return {"type": "number", "label": key}
    if isinstance(value, str):
        return {"type": "string", "label": key}
    return {"type": "string", "label": key}


def _merge_schema(
    values: dict[str, Any],
    schema_override: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for key, val in values.items():
        base = _infer_schema_field(key, val)
        extra = schema_override.get(key) if isinstance(schema_override, dict) else None
        if isinstance(extra, dict):
            base.update({k: v for k, v in extra.items() if v is not None})
        merged[key] = base
    return merged


def _coerce_for_key(
    key: str,
    raw: Any,
    *,
    original: Any,
    schema_field: dict[str, Any],
) -> Any:
    t = str(schema_field.get("type") or "").lower()
    if t == "boolean" or isinstance(original, bool):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if t == "integer":
        return int(round(float(raw)))
    if t == "number":
        return float(raw)
    if t == "string":
        return str(raw)
    if isinstance(original, bool):
        return _coerce_for_key(key, raw, original=False, schema_field={"type": "boolean"})
    if isinstance(original, int) and not isinstance(original, bool):
        return int(round(float(raw)))
    if isinstance(original, float):
        return float(raw)
    return str(raw)


def extract_strategy_params(source: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]], bool]:
    """소스에서 STRATEGY_PARAMS 및 선택적 STRATEGY_PARAM_SCHEMA를 읽는다.

    Returns:
        (values, schema_for_ui, supported) — supported False이면 values는 {}.
    """
    stripped = source.strip()
    if not stripped:
        return {}, {}, False
    try:
        tree = ast.parse(stripped)
    except SyntaxError as exc:
        raise StrategyParamsError(f"invalid Python syntax: {exc}") from exc

    params_node: ast.expr | None = None
    schema_node: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "STRATEGY_PARAMS":
                params_node = node.value
            elif node.target.id == "STRATEGY_PARAM_SCHEMA":
                schema_node = node.value
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                if target.id == "STRATEGY_PARAMS":
                    params_node = node.value
                elif target.id == "STRATEGY_PARAM_SCHEMA":
                    schema_node = node.value

    if params_node is None:
        return {}, {}, False

    try:
        values = _eval_dict_literal(params_node)
    except StrategyParamsError:
        return {}, {}, False

    schema_override: dict[str, Any] | None = None
    if schema_node is not None:
        try:
            raw_schema = _eval_dict_literal(schema_node)
            schema_override = raw_schema if isinstance(raw_schema, dict) else None
        except StrategyParamsError:
            schema_override = None

    schema_ui = _merge_schema(values, schema_override)
    return values, schema_ui, True


def apply_strategy_params(source: str, param_values: dict[str, Any]) -> str:
    """STRATEGY_PARAMS 할당문만 갱신한 전체 소스를 반환한다."""
    stripped = source.strip()
    if not stripped:
        raise StrategyParamsError("empty source")

    try:
        tree = ast.parse(stripped)
    except SyntaxError as exc:
        raise StrategyParamsError(f"invalid Python syntax: {exc}") from exc

    current, schema_ui, supported = extract_strategy_params(stripped)
    if not supported or not current:
        raise StrategyParamsError("STRATEGY_PARAMS not found or not extractable")

    extra_keys = set(param_values) - set(current)
    if extra_keys:
        raise StrategyParamsError(f"unknown parameter keys: {sorted(extra_keys)}")

    new_values: dict[str, Any] = {}
    for key, orig in current.items():
        if key not in param_values:
            new_values[key] = orig
            continue
        raw = param_values[key]
        field = schema_ui.get(key, {})
        new_values[key] = _coerce_for_key(key, raw, original=orig, schema_field=field)

    class _Patch(ast.NodeTransformer):
        def visit_Assign(self, node: ast.Assign) -> ast.Assign:
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "STRATEGY_PARAMS"
            ):
                keys = list(new_values.keys())
                node.value = ast.Dict(
                    keys=[ast.Constant(value=k) for k in keys],
                    values=[_value_to_ast(new_values[k]) for k in keys],
                )
            return node

        def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:
            if isinstance(node.target, ast.Name) and node.target.id == "STRATEGY_PARAMS" and node.value:
                keys = list(new_values.keys())
                node.value = ast.Dict(
                    keys=[ast.Constant(value=k) for k in keys],
                    values=[_value_to_ast(new_values[k]) for k in keys],
                )
            return node

    patched = _Patch().visit(tree)
    ast.fix_missing_locations(patched)
    return ast.unparse(patched) + "\n"
