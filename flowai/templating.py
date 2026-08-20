from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from typing import Any

PLACEHOLDER = re.compile(r"{{\s*([\w.$-]+)\s*}}")


def resolve_path(value: Any, path: str, default: Any = None) -> Any:
    cleaned = path.strip()
    if cleaned in {"", "$", "."}:
        return value
    cleaned = cleaned.removeprefix("$.")
    current = value
    for part in cleaned.split("."):
        if not part:
            continue
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return default
            current = current[index]
        else:
            return default
    return current


def stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2)


def render_template(template: str, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        found = resolve_path(context, path, default=f"{{{{{path}}}}}")
        return stringify(found)

    return PLACEHOLDER.sub(replace, template)


class UnsafeExpression(ValueError):
    pass


def safe_eval(expression: str, context: dict[str, Any]) -> Any:
    text = expression.strip()
    if not text:
        return True
    tree = ast.parse(text, mode="eval")
    return _evaluate(tree.body, context)


def _evaluate(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        aliases = {"true": True, "false": False, "null": None}
        if node.id in aliases:
            return aliases[node.id]
        if node.id in context:
            return context[node.id]
        raise UnsafeExpression(f"Невідома змінна: {node.id}")
    if isinstance(node, ast.Attribute):
        parent = _evaluate(node.value, context)
        if isinstance(parent, Mapping):
            return parent.get(node.attr)
        raise UnsafeExpression("Доступ до атрибутів дозволений лише для даних Flow")
    if isinstance(node, ast.Subscript):
        parent = _evaluate(node.value, context)
        key = _evaluate(node.slice, context)
        try:
            return parent[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.List):
        return [_evaluate(item, context) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(item, context) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _evaluate(key, context): _evaluate(value, context)
            for key, value in zip(node.keys, node.values, strict=True)
        }
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _evaluate(node.operand, context)
    if isinstance(node, ast.BoolOp):
        values = [_evaluate(item, context) for item in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, context)
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator, context)
            if isinstance(operator, ast.Eq):
                matched = left == right
            elif isinstance(operator, ast.NotEq):
                matched = left != right
            elif isinstance(operator, ast.Gt):
                matched = left > right
            elif isinstance(operator, ast.GtE):
                matched = left >= right
            elif isinstance(operator, ast.Lt):
                matched = left < right
            elif isinstance(operator, ast.LtE):
                matched = left <= right
            elif isinstance(operator, ast.In):
                matched = left in right
            elif isinstance(operator, ast.NotIn):
                matched = left not in right
            elif isinstance(operator, ast.Is):
                matched = left is right
            elif isinstance(operator, ast.IsNot):
                matched = left is not right
            else:
                raise UnsafeExpression("Цей оператор не підтримується")
            if not matched:
                return False
            left = right
        return True
    raise UnsafeExpression(f"Недозволений вираз: {type(node).__name__}")


def extract_json(text: str) -> Any | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    starts = [index for index in (stripped.find("{"), stripped.find("[")) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in range(len(stripped), start, -1):
        try:
            return json.loads(stripped[start:end])
        except json.JSONDecodeError:
            continue
    return None
