# SPDX-License-Identifier: LGPL-3.0-or-later
"""安全表达式引擎：元数据 DSL 的前置条件/不变量/评测断言求值。
白名单 AST，无 eval/exec；只允许上下文提供的函数与字段访问（§12 健壮性）。
支持子集：字面量/名字/属性/下标/算术/比较/and or not/函数调用/列表/字典/条件表达式。
内置：len round min max abs sum(coll[, 'expr'])。金额 Decimal 与 float 混算自动转 Decimal。"""
from __future__ import annotations
import ast
from decimal import Decimal
from typing import Any, Callable

_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Call, ast.Name, ast.Load, ast.Constant, ast.Attribute, ast.Subscript,
    ast.List, ast.Tuple, ast.Dict, ast.IfExp, ast.Slice,
)


class ExprError(Exception):
    pass


def _maybe_num(x):
    """数字字符串按 Decimal 参与运算/比较（管线 data 为 JSON 序列化后的字符串金额）。"""
    if isinstance(x, str):
        try:
            return Decimal(x)
        except Exception:
            return x
    return x


def _coerce(a, b):
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        def d(x):
            return x if isinstance(x, Decimal) else Decimal(str(x))
        return d(a), d(b)
    return a, b


def _round(x, n=0):
    if isinstance(x, Decimal):
        return x.quantize(Decimal(1).scaleb(-int(n)), rounding="ROUND_HALF_UP")
    return round(x, int(n))


def _sum(coll, expr: str | None = None, _ctx=None):
    items = list(coll)
    if expr is None:
        out = Decimal("0") if any(isinstance(i, Decimal) for i in items) else 0
        for i in items:
            out = out + i
        return out
    total = Decimal("0")
    for item in items:
        child = dict(_ctx or {})
        if isinstance(item, dict):
            child.update(item)
        else:
            child.update({k: getattr(item, k) for k in vars(item) if not k.startswith("_")} if hasattr(item, "__dict__") else {})
        child["item"] = item
        v = evaluate(expr, child)
        total += v if isinstance(v, Decimal) else Decimal(str(v))
    return total


class Evaluator:
    def __init__(self, ctx: dict[str, Any]):
        self.ctx = ctx
        self.builtins: dict[str, Callable] = {
            "len": len, "min": min, "max": max, "abs": abs, "round": _round,
        }

    def eval(self, source: str):
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as e:
            raise ExprError(f"syntax: {e}") from e
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                raise ExprError(f"forbidden node: {type(node).__name__}")
        return self._ev(tree.body)

    def _ev(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ("true", "false", "null"):
                return {"true": True, "false": False, "null": None}[node.id]
            if node.id in self.ctx:
                return self.ctx[node.id]
            if node.id in self.builtins:
                return self.builtins[node.id]
            if node.id == "sum":
                return lambda coll, expr=None: _sum(coll, expr, self.ctx)
            raise ExprError(f"unknown name: {node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ExprError("private attribute access forbidden")
            obj = self._ev(node.value)
            if isinstance(obj, dict):
                if node.attr not in obj:
                    raise ExprError(f"missing key: {node.attr}")
                return obj[node.attr]
            return getattr(obj, node.attr)
        if isinstance(node, ast.Subscript):
            obj = self._ev(node.value)
            idx = self._ev(node.slice)
            return obj[idx]
        if isinstance(node, ast.Slice):
            return slice(
                self._ev(node.lower) if node.lower else None,
                self._ev(node.upper) if node.upper else None,
            )
        if isinstance(node, (ast.List, ast.Tuple)):
            vals = [self._ev(e) for e in node.elts]
            return vals if isinstance(node, ast.List) else tuple(vals)
        if isinstance(node, ast.Dict):
            return {self._ev(k): self._ev(v) for k, v in zip(node.keys, node.values)}
        if isinstance(node, ast.UnaryOp):
            v = self._ev(node.operand)
            if isinstance(node.op, ast.Not):
                return not v
            return -v
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = True
                for v in node.values:
                    result = self._ev(v)
                    if not result:
                        return result
                return result
            result = False
            for v in node.values:
                result = self._ev(v)
                if result:
                    return result
            return result
        if isinstance(node, ast.BinOp):
            lv, rv = self._ev(node.left), self._ev(node.right)
            def _is_num(v):
                return isinstance(v, (Decimal, int, float)) and not isinstance(v, bool)
            if isinstance(node.op, ast.Add) and not (_is_num(_maybe_num(lv)) and _is_num(_maybe_num(rv))):
                return lv + rv  # 非数字字符串保留拼接语义
            a, b = _coerce(_maybe_num(lv), _maybe_num(rv))
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                return a / b
            if isinstance(node.op, ast.Mod):
                return a % b
            raise ExprError("unsupported binop")
        if isinstance(node, ast.Compare):
            left = self._ev(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._ev(comparator)
                l, r = _coerce(_maybe_num(left), _maybe_num(right))
                ok = {
                    ast.Eq: lambda: l == r, ast.NotEq: lambda: l != r,
                    ast.Lt: lambda: l < r, ast.LtE: lambda: l <= r,
                    ast.Gt: lambda: l > r, ast.GtE: lambda: l >= r,
                    ast.In: lambda: l in r, ast.NotIn: lambda: l not in r,
                }[type(op)]()
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._ev(node.body) if self._ev(node.test) else self._ev(node.orelse)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExprError("only named function calls allowed")
            fn = self._ev(node.func)
            if not callable(fn):
                raise ExprError(f"not callable: {node.func.id}")
            args = [self._ev(a) for a in node.args]
            kwargs = {k.arg: self._ev(k.value) for k in node.keywords}
            return fn(*args, **kwargs)
        raise ExprError(f"unhandled: {type(node).__name__}")


def evaluate(source: str, ctx: dict[str, Any]):
    return Evaluator(ctx).eval(source)


def check(source: str, ctx: dict[str, Any]) -> tuple[bool, str]:
    """断言/前置条件求值：返回 (通过?, 错误信息)。表达式异常=不通过（fail-closed）。"""
    try:
        return bool(evaluate(source, ctx)), ""
    except ExprError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 fail-closed
        return False, f"{type(e).__name__}: {e}"
