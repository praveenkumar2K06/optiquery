"""Serialization helpers for AST nodes into JSON-compatible dicts and trees."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from ..ast.nodes import (
    Assignment,
    BetweenExpr,
    BinaryOp,
    CaseExpr,
    CaseWhen,
    ColumnDef,
    DeleteStatement,
    Expr,
    FunctionCall,
    Identifier,
    InListExpr,
    InsertStatement,
    IsNullExpr,
    JoinClause,
    LikeExpr,
    Literal,
    Node,
    OrderByItem,
    QualifiedIdentifier,
    SelectItem,
    SelectStatement,
    Star,
    Statement,
    TableRef,
    UnaryOp,
    UpdateStatement,
)


def _span_to_dict(span) -> Optional[Dict[str, Any]]:
    if span is None:
        return None
    return {
        "start": {"line": span.start.line, "column": span.start.column, "offset": span.start.offset},
        "end": {"line": span.end.line, "column": span.end.column, "offset": span.end.offset},
    }


def ast_to_dict(node: Optional[Node]) -> Optional[Dict[str, Any]]:
    """Convert an AST node into a clean, hierarchical JSON-serializable dictionary."""
    if node is None:
        return None

    node_type = type(node).__name__
    res: Dict[str, Any] = {"type": node_type}
    if hasattr(node, "span") and node.span:
        res["span"] = _span_to_dict(node.span)

    if isinstance(node, SelectStatement):
        res["distinct"] = node.distinct
        res["projections"] = [ast_to_dict(item) for item in node.items]
        res["from"] = ast_to_dict(node.from_table) if node.from_table else None
        res["joins"] = [ast_to_dict(j) for j in node.joins]
        res["where"] = ast_to_dict(node.where) if node.where else None
        res["groupBy"] = [ast_to_dict(g) for g in node.group_by]
        res["having"] = ast_to_dict(node.having) if node.having else None
        res["orderBy"] = [ast_to_dict(o) for o in node.order_by]
        res["limit"] = ast_to_dict(node.limit) if node.limit else None
        res["offset"] = ast_to_dict(node.offset) if node.offset else None
        return res

    if isinstance(node, SelectItem):
        res["expr"] = ast_to_dict(node.expr)
        res["alias"] = node.alias
        return res

    if isinstance(node, TableRef):
        res["name"] = node.name
        res["alias"] = node.alias
        return res

    if isinstance(node, JoinClause):
        res["joinType"] = node.join_type
        res["table"] = ast_to_dict(node.table)
        res["on"] = ast_to_dict(node.condition) if node.condition else None
        return res

    if isinstance(node, OrderByItem):
        res["expr"] = ast_to_dict(node.expr)
        res["descending"] = node.descending
        return res

    if isinstance(node, Literal):
        res["value"] = node.value
        res["kind"] = node.kind
        return res

    if isinstance(node, (Identifier, QualifiedIdentifier)):
        res["name"] = node.name
        res["qualifier"] = node.qualifier
        res["qualifiedName"] = node.qualified_name
        return res

    if isinstance(node, Star):
        res["qualifier"] = node.qualifier
        return res

    if isinstance(node, BinaryOp):
        res["op"] = node.op
        res["left"] = ast_to_dict(node.left)
        res["right"] = ast_to_dict(node.right)
        return res

    if isinstance(node, UnaryOp):
        res["op"] = node.op
        res["operand"] = ast_to_dict(node.operand)
        return res

    if isinstance(node, FunctionCall):
        res["name"] = node.name
        res["distinct"] = node.distinct
        res["args"] = [ast_to_dict(a) for a in node.args]
        return res

    if isinstance(node, InListExpr):
        res["negated"] = node.negated
        res["operand"] = ast_to_dict(node.operand)
        res["options"] = [ast_to_dict(o) for o in node.options]
        return res

    if isinstance(node, BetweenExpr):
        res["negated"] = node.negated
        res["operand"] = ast_to_dict(node.operand)
        res["low"] = ast_to_dict(node.low)
        res["high"] = ast_to_dict(node.high)
        return res

    if isinstance(node, LikeExpr):
        res["negated"] = node.negated
        res["operand"] = ast_to_dict(node.operand)
        res["pattern"] = ast_to_dict(node.pattern)
        return res

    if isinstance(node, IsNullExpr):
        res["negated"] = node.negated
        res["operand"] = ast_to_dict(node.operand)
        return res

    if isinstance(node, CaseExpr):
        res["operand"] = ast_to_dict(node.operand) if node.operand else None
        res["whens"] = [ast_to_dict(w) for w in node.whens]
        res["default"] = ast_to_dict(node.default) if node.default else None
        return res

    if isinstance(node, CaseWhen):
        res["condition"] = ast_to_dict(node.condition)
        res["result"] = ast_to_dict(node.result)
        return res

    if isinstance(node, InsertStatement):
        res["table"] = ast_to_dict(node.table)
        res["columns"] = node.columns
        res["rows"] = [[ast_to_dict(e) for e in row] for row in node.rows]
        return res

    if isinstance(node, UpdateStatement):
        res["table"] = ast_to_dict(node.table)
        res["assignments"] = [ast_to_dict(a) for a in node.assignments]
        res["where"] = ast_to_dict(node.where) if node.where else None
        return res

    if isinstance(node, Assignment):
        res["field"] = node.field
        res["value"] = ast_to_dict(node.value)
        return res

    if isinstance(node, DeleteStatement):
        res["table"] = ast_to_dict(node.table)
        res["where"] = ast_to_dict(node.where) if node.where else None
        return res

    # Fallback generic representation
    res["repr"] = repr(node)
    return res

