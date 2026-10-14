"""Human-readable serialization for plans and expressions.

These helpers power explanations, diagnostics, and golden tests. They are
deliberately backend-agnostic and preserve the logical structure of plans.
"""
from __future__ import annotations

import json

from .expr import (
    BetweenExpr,
    BinaryExpr,
    CaseExpr,
    CaseWhen,
    ColumnRef,
    FunctionExpr,
    IRExpr,
    InListExpr,
    IsNullExpr,
    LikeExpr,
    LiteralValue,
    UnaryExpr,
)
from .plan import LogicalPlan


def expr_to_string(expr: IRExpr) -> str:
    """Serialize an IR expression to a compact SQL-like string."""
    if expr is None:
        return "NULL"
    if isinstance(expr, ColumnRef):
        return expr.qualified_name if expr.qualified_name else expr.name
    if isinstance(expr, LiteralValue):
        if expr.kind == "string":
            return "'" + str(expr.value).replace("'", "''") + "'"
        if expr.kind == "boolean":
            return "TRUE" if expr.value else "FALSE"
        if expr.kind == "null":
            return "NULL"
        return str(expr.value)
    if isinstance(expr, UnaryExpr):
        if expr.op == "NOT":
            return f"NOT {expr_to_string(expr.operand)}"
        return f"{expr.op}{expr_to_string(expr.operand)}"
    if isinstance(expr, BinaryExpr):
        return f"({expr_to_string(expr.left)} {expr.op} {expr_to_string(expr.right)})"
    if isinstance(expr, FunctionExpr):
        args = ", ".join(expr_to_string(a) for a in expr.args)
        if expr.distinct and expr.name.upper() == "COUNT":
            return f"COUNT(DISTINCT {args})"
        return f"{expr.name}({args})"
    if isinstance(expr, InListExpr):
        opts = ", ".join(expr_to_string(o) for o in expr.options)
        prefix = "NOT IN" if expr.negated else "IN"
        return f"{expr_to_string(expr.operand)} {prefix} ({opts})"
    if isinstance(expr, BetweenExpr):
        prefix = "NOT BETWEEN" if expr.negated else "BETWEEN"
        return (
            f"{expr_to_string(expr.operand)} {prefix} "
            f"{expr_to_string(expr.low)} AND {expr_to_string(expr.high)}"
        )
    if isinstance(expr, LikeExpr):
        prefix = "NOT LIKE" if expr.negated else "LIKE"
        return f"{expr_to_string(expr.operand)} {prefix} {expr_to_string(expr.pattern)}"
    if isinstance(expr, IsNullExpr):
        prefix = "IS NOT NULL" if expr.negated else "IS NULL"
        return f"{expr_to_string(expr.operand)} {prefix}"
    if isinstance(expr, CaseExpr):
        parts = ["CASE"]
        for when in expr.whens:
            parts.append(
                f"WHEN {expr_to_string(when.condition)} THEN {expr_to_string(when.result)}"
            )
        if expr.default is not None:
            parts.append(f"ELSE {expr_to_string(expr.default)}")
        parts.append("END")
        return " ".join(parts)
    return str(expr)


def expr_to_json(expr: IRExpr):
    """Return a JSON-serializable representation of an expression."""
    if expr is None:
        return {"kind": "null"}
    if isinstance(expr, ColumnRef):
        return {
            "kind": "column",
            "name": expr.name,
            "source": expr.source,
        }
    if isinstance(expr, LiteralValue):
        return {
            "kind": "literal",
            "type": expr.type.value,
            "value": expr.value,
            "logical_kind": expr.kind,
        }
    if isinstance(expr, UnaryExpr):
        return {
            "kind": "unary",
            "op": expr.op,
            "operand": expr_to_json(expr.operand),
        }
    if isinstance(expr, BinaryExpr):
        return {
            "kind": "binary",
            "op": expr.op,
            "left": expr_to_json(expr.left),
            "right": expr_to_json(expr.right),
        }
    if isinstance(expr, FunctionExpr):
        return {
            "kind": "function",
            "name": expr.name,
            "args": [expr_to_json(a) for a in expr.args],
            "distinct": expr.distinct,
            "aggregate": expr.aggregate,
        }
    if isinstance(expr, InListExpr):
        return {
            "kind": "in",
            "operand": expr_to_json(expr.operand),
            "options": [expr_to_json(o) for o in expr.options],
            "negated": expr.negated,
        }
    if isinstance(expr, BetweenExpr):
        return {
            "kind": "between",
            "operand": expr_to_json(expr.operand),
            "low": expr_to_json(expr.low),
            "high": expr_to_json(expr.high),
            "negated": expr.negated,
        }
    if isinstance(expr, LikeExpr):
        return {
            "kind": "like",
            "operand": expr_to_json(expr.operand),
            "pattern": expr_to_json(expr.pattern),
            "negated": expr.negated,
        }
    if isinstance(expr, IsNullExpr):
        return {
            "kind": "isnull",
            "operand": expr_to_json(expr.operand),
            "negated": expr.negated,
        }
    if isinstance(expr, CaseExpr):
        return {
            "kind": "case",
            "whens": [
                {
                    "condition": expr_to_json(w.condition),
                    "result": expr_to_json(w.result),
                }
                for w in expr.whens
            ],
            "default": expr_to_json(expr.default) if expr.default is not None else None,
        }
    return {"kind": "unknown"}


def plan_to_string(plan: LogicalPlan) -> str:
    """Return a tree-like string representation of the logical plan."""
    lines: List[str] = []

    def walk(node: LogicalPlan, indent: int = 0):
        pad = "  " * indent
        lines.append(f"{pad}{node.node_name()}")
        for child in node.inputs():
            walk(child, indent + 1)

    walk(plan)
    return "\n".join(lines)


def plan_to_json(plan: LogicalPlan):
    """Return a JSON-serializable representation of the logical plan."""
    return _plan_to_json(plan)


def _plan_to_json(plan: LogicalPlan):
    if hasattr(plan, "to_json"):
        return plan.to_json()
    # Generic serializer for known node types.
    base = {"kind": plan.node_name()}
    if hasattr(plan, "predicate") and getattr(plan, "predicate") is not None:
        base["predicate"] = expr_to_json(getattr(plan, "predicate"))
    if hasattr(plan, "items") and getattr(plan, "items"):
        items = []
        for name, expr in getattr(plan, "items"):
            items.append({"name": name, "expr": expr_to_json(expr)})
        base["items"] = items
    if hasattr(plan, "group_keys") and getattr(plan, "group_keys"):
        base["groupKeys"] = [expr_to_json(e) for e in getattr(plan, "group_keys")]
    if hasattr(plan, "aggregates") and getattr(plan, "aggregates"):
        aggs = []
        for name, expr in getattr(plan, "aggregates"):
            aggs.append({"name": name, "expr": expr_to_json(expr)})
        base["aggregates"] = aggs
    if hasattr(plan, "keys") and getattr(plan, "keys"):
        keys = []
        for expr, desc in getattr(plan, "keys"):
            keys.append({"expr": expr_to_json(expr), "desc": desc})
        base["keys"] = keys
    if hasattr(plan, "count"):
        base["count"] = getattr(plan, "count")
    if hasattr(plan, "collection"):
        base["collection"] = getattr(plan, "collection")
    if hasattr(plan, "alias"):
        base["alias"] = getattr(plan, "alias")
    if hasattr(plan, "projection"):
        base["projection"] = getattr(plan, "projection")
    if hasattr(plan, "condition") and getattr(plan, "condition") is not None:
        base["condition"] = expr_to_json(getattr(plan, "condition"))
    if hasattr(plan, "join_type"):
        base["joinType"] = getattr(plan, "join_type")
    base["inputs"] = [_plan_to_json(c) for c in plan.inputs()]
    return base
