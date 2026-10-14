"""Constant folding and algebraic simplification pass.

Evaluates constant (literal-only) subexpressions at compile time and applies
safe Boolean simplifications. NULL semantics follow three-valued logic: an
operation involving NULL that would be unknown stays NULL rather than being
folded to a concrete value.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from ...ir import (
    BinaryExpr,
    IRExpr,
    InListExpr,
    IsNullExpr,
    LiteralValue,
    UnaryExpr,
)
from ...ir.plan import Filter, LogicalPlan, Project
from ...metadata import FieldType


class ConstantFoldingPass:
    """Fold constant subexpressions and simplify Boolean expressions."""

    name = "constant_folding"

    def __init__(self):
        self.changes = 0

    def apply(self, plan: LogicalPlan) -> LogicalPlan:
        self.changes = 0
        return self._rewrite(plan)

    def _rewrite(self, node: LogicalPlan) -> LogicalPlan:
        # Rewrite children first and relink any that changed.
        children = node.inputs()
        if children:
            new_children = [self._rewrite(child) for child in children]
            if any(nc is not c for nc, c in zip(new_children, children)):
                node = node.with_inputs(new_children)

        if isinstance(node, Filter):
            new_pred = self._fold(node.predicate)
            if isinstance(new_pred, LiteralValue) and new_pred.kind == "boolean":
                if new_pred.value is True:
                    # Filter always true -> drop the node entirely.
                    self.changes += 1
                    return node.input
            if new_pred is not node.predicate:
                node.predicate = new_pred
                self.changes += 1
        elif isinstance(node, Project):
            updated = []
            changed = False
            for name, expr in node.items:
                folded = self._fold(expr)
                if folded is not expr:
                    changed = True
                updated.append((name, folded))
            if changed:
                node.items = updated
                self.changes += 1
        return node

    def _fold(self, expr: IRExpr) -> IRExpr:
        if isinstance(expr, BinaryExpr):
            left = self._fold(expr.left)
            right = self._fold(expr.right)
            if left is not expr.left or right is not expr.right:
                expr = replace(expr, left=left, right=right)
            folded = self._eval_binary(expr)
            return folded if folded is not None else expr

        if isinstance(expr, UnaryExpr):
            operand = self._fold(expr.operand)
            if operand is not expr.operand:
                expr = replace(expr, operand=operand)
            folded = self._eval_unary(expr)
            return folded if folded is not None else expr

        if isinstance(expr, InListExpr):
            operand = self._fold(expr.operand)
            options = tuple(self._fold(o) for o in expr.options)
            if operand is not expr.operand or options != expr.options:
                return replace(expr, operand=operand, options=options)
            return expr

        if isinstance(expr, IsNullExpr):
            operand = self._fold(expr.operand)
            if isinstance(operand, LiteralValue):
                is_null = operand.kind == "null"
                result = (not is_null) if expr.negated else is_null
                return _bool_literal(result)
            return replace(expr, operand=operand) if operand is not expr.operand else expr

        return expr

    def _eval_binary(self, expr: BinaryExpr) -> Optional[IRExpr]:
        left, right = expr.left, expr.right
        l_lit = isinstance(left, LiteralValue)
        r_lit = isinstance(right, LiteralValue)

        # Algebraic identities that are always safe.
        if expr.op in ("*", "/") and r_lit and right.value == 1:
            return left
        if expr.op == "+" and r_lit and right.value == 0:
            return left
        if expr.op == "-" and r_lit and right.value == 0:
            return left
        if expr.op == "*" and r_lit and right.value == 0 and not left.nullable:
            return _literal(0, "integer", FieldType.INTEGER)
        if expr.op == "*" and l_lit and left.value == 1:
            return right

        # Boolean short-circuit with a literal operand.
        if expr.op == "AND":
            if l_lit and left.kind == "boolean":
                return right if left.value else _bool_literal(False)
            if r_lit and right.kind == "boolean":
                return left if right.value else _bool_literal(False)
        if expr.op == "OR":
            if l_lit and left.kind == "boolean":
                return _bool_literal(True) if left.value else right
            if r_lit and right.kind == "boolean":
                return _bool_literal(True) if right.value else left

        # Arithmetic / comparison folding when both sides are literals.
        if l_lit and r_lit:
            return self._fold_literals(expr.op, left, right)

        return None

    def _fold_literals(
        self, op: str, left: LiteralValue, right: LiteralValue
    ) -> Optional[IRExpr]:
        # NULL propagation for arithmetic: result is NULL, not a number.
        if left.kind == "null" and op in ("+", "-", "*", "/", "%"):
            return _null_literal()
        if right.kind == "null" and op in ("+", "-", "*", "/", "%"):
            return _null_literal()

        try:
            if op == "+":
                return _num_literal(left.value + right.value, _num_kind(left, right))
            if op == "-":
                return _num_literal(left.value - right.value, _num_kind(left, right))
            if op == "*":
                return _num_literal(left.value * right.value, _num_kind(left, right))
            if op == "/":
                if right.value == 0:
                    return None
                return _num_literal(left.value / right.value, "decimal")
            if op == "%":
                if right.value == 0:
                    return None
                return _num_literal(left.value % right.value, _num_kind(left, right))
            if op in ("=", "!=", "<", ">", "<=", ">="):
                return _bool_literal(_compare(op, left.value, right.value))
        except TypeError:
            return None
        return None

    def _eval_unary(self, expr: UnaryExpr) -> Optional[IRExpr]:
        operand = expr.operand
        if not isinstance(operand, LiteralValue):
            # NOT (NOT x) -> x
            if expr.op == "NOT" and isinstance(operand, UnaryExpr) and operand.op == "NOT":
                return operand.operand
            return None
        if expr.op == "NOT":
            if operand.kind == "null":
                return _null_literal()
            return _bool_literal(not bool(operand.value))
        if expr.op == "-":
            if operand.kind == "null":
                return _null_literal()
            if operand.type.is_numeric or operand.kind in ("integer", "decimal"):
                kind = "decimal" if operand.kind == "decimal" else "integer"
                t = FieldType.DECIMAL if kind == "decimal" else FieldType.INTEGER
                return _literal(-operand.value, kind, t)
        return None


# ---------------------------------------------------------------------------
# Literal helpers
# ---------------------------------------------------------------------------


def _literal(value, kind: str, t: FieldType = FieldType.ANY) -> LiteralValue:
    return LiteralValue(value=value, kind=kind, type=t)


def _num_literal(value, kind: str) -> LiteralValue:
    t = FieldType.DECIMAL if kind == "decimal" else FieldType.INTEGER
    return LiteralValue(value=value, kind=kind, type=t)


def _bool_literal(value: bool) -> LiteralValue:
    return LiteralValue(value=value, kind="boolean", type=FieldType.BOOLEAN)


def _null_literal() -> LiteralValue:
    return LiteralValue(value=None, kind="null", type=FieldType.NULL)


def _num_kind(left: LiteralValue, right: LiteralValue) -> str:
    if left.kind == "decimal" or right.kind == "decimal":
        return "decimal"
    return "integer"


def _compare(op: str, left, right) -> bool:
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op == "<=":
        return left <= right
    if op == ">=":
        return left >= right
    raise ValueError(op)
