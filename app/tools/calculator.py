"""Safe calculator utility for research tooling."""

from __future__ import annotations

import ast
import operator
from typing import Any

from app.tools.base import ToolValidationError

_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def calculate(expression: str) -> float:
    """Evaluate a numeric arithmetic expression safely (no names/calls)."""
    cleaned = expression.strip()
    if not cleaned or len(cleaned) > 200:
        raise ToolValidationError("Invalid calculator expression")

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ToolValidationError("Invalid calculator expression") from exc

    value = _eval_node(tree.body)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolValidationError("Calculator result must be numeric")
    return float(value)


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return float(_UNARY_OPS[type(node.op)](_eval_node(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ToolValidationError("Division by zero")
        return float(_BIN_OPS[type(node.op)](left, right))
    raise ToolValidationError("Unsupported calculator expression")
