"""Exact arithmetic for crew agents - an AST walk, never ``eval``.

Models are fluent and wrong on long multiplication; a tool that is exact is
the cheap fix. Integers stay integers (Python big ints), so
``987654321 * 123456789`` comes back digit-exact. Only numeric literals,
arithmetic operators and a short list of math functions are accepted; names,
attributes, calls to anything else, and oversized powers are refused with a
reason instead of evaluated.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable
from typing import Any

MAX_EXPR_CHARS = 500
MAX_POW_EXPONENT = 10_000
# Below CPython's default int->str limit (4300 digits), so a result that
# passes the cap can always be printed.
MAX_RESULT_DIGITS = 4_000

_BIN: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "gcd": math.gcd,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_MAX_BITS = int(MAX_RESULT_DIGITS * 3.33) + 8
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


class CalcError(ValueError):
    pass


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_POW_EXPONENT:
                raise CalcError(f"exponent {right} is above the {MAX_POW_EXPONENT} cap")
            if isinstance(left, int) and left.bit_length() * abs(right) > _MAX_BITS:
                raise CalcError(f"result would have more than {MAX_RESULT_DIGITS} digits")
        if isinstance(node.op, ast.Mult) and isinstance(left, int) and isinstance(right, int):
            if left.bit_length() + right.bit_length() > _MAX_BITS + 64:
                raise CalcError(f"result would have more than {MAX_RESULT_DIGITS} digits")
        try:
            return _BIN[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise CalcError("division by zero") from exc
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 5_000:
            raise CalcError("factorial argument above 5000")
        return _FUNCS[node.func.id](*args)
    raise CalcError(f"unsupported expression element: {type(node).__name__}")


def evaluate(expr: str) -> str:
    """Return the exact result as text, or raise :class:`CalcError`."""
    text = _THOUSANDS.sub("", (expr or "").strip().replace("^", "**"))
    if not text:
        raise CalcError("expression required")
    if len(text) > MAX_EXPR_CHARS:
        raise CalcError(f"expression longer than {MAX_EXPR_CHARS} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"not an arithmetic expression: {exc.msg}") from exc
    try:
        value = _eval(tree)
    except RecursionError as exc:
        raise CalcError("expression nested too deeply") from exc
    except (OverflowError, ValueError, TypeError) as exc:
        if isinstance(exc, CalcError):
            raise
        raise CalcError(str(exc)) from exc
    if isinstance(value, complex):
        raise CalcError("result is not a real number")
    if isinstance(value, int) and value.bit_length() > _MAX_BITS:
        raise CalcError(f"result has more than {MAX_RESULT_DIGITS} digits")
    try:
        text_value = str(value)
    except ValueError as exc:  # int->str digit limit
        raise CalcError(f"result has more than {MAX_RESULT_DIGITS} digits") from exc
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return text_value
