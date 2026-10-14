"""Transforms a semantically-analyzed AST into a logical execution plan."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..ast import (
    BinaryOp,
    CaseExpr,
    CaseWhen as ASTCaseWhen,
    DeleteStatement,
    Expr,
    FunctionCall,
    Identifier,
    InsertStatement,
    IsNullExpr,
    Literal,
    SelectItem,
    SelectStatement,
    Star,
    UnaryOp,
    UpdateStatement,
)
from ..ir import (
    BetweenExpr,
    BinaryExpr,
    CaseExpr as IRCaseExpr,
    CaseWhen as IRCaseWhen,
    ColumnRef,
    FunctionExpr,
    InListExpr,
    IsNullExpr as IRIsNullExpr,
    LikeExpr,
    LiteralValue,
    LogicalPlan,
    Distinct,
    Filter,
    Group,
    Insert,
    Limit,
    Project,
    Scan,
    Skip,
    Sort,
    Update,
    Delete,
    UnaryExpr,
)
from ..metadata import FieldType

_AGGREGATE_MAP = {
    "COUNT": True,
    "SUM": True,
    "AVG": True,
    "MIN": True,
    "MAX": True,
}


class PlanLowerer:
    """Lowers AST statements to IR logical plans."""

    def __init__(self):
        self._next_alias = 0

    # -- Entry points ------------------------------------------------------

    def lower(self, stmt) -> LogicalPlan:
        if isinstance(stmt, SelectStatement):
            return self._lower_select(stmt)
        if isinstance(stmt, InsertStatement):
            return self._lower_insert(stmt)
        if isinstance(stmt, UpdateStatement):
            return self._lower_update(stmt)
        if isinstance(stmt, DeleteStatement):
            return self._lower_delete(stmt)
        # Fallback: return a no-op Scan
        return Scan(collection="unknown")

    def _lower_select(self, stmt: SelectStatement) -> LogicalPlan:
        plan: LogicalPlan = self._build_base_scan(stmt)

        if stmt.where is not None:
            plan = Filter(predicate=self._lower_expr(stmt.where), input=plan)

        is_aggregate = bool(stmt.group_by) or self._stmt_has_aggregates(stmt)
        if is_aggregate:
            group_keys = [self._lower_expr(g) for g in stmt.group_by]
            aggregates: List[Tuple[str, FunctionExpr]] = []
            # Collect aggregates from projection.
            for item in stmt.items:
                aggregates.extend(self._extract_aggregates(item.expr, self._item_name(item)))
            group_node = Group(group_keys=group_keys, aggregates=aggregates, input=plan)
            plan = group_node
            if stmt.having is not None:
                plan = Filter(predicate=self._lower_expr(stmt.having), input=plan)
            # For GROUP BY, projection selects the group keys and aggregates.
            project_items = self._build_group_projection(stmt, group_node)
            plan = Project(items=project_items, input=plan)
        else:
            # Non-aggregate select: project computed columns.
            project_items = []
            for item in stmt.items:
                if isinstance(item.expr, Star):
                    # For a plain star, include all referenced fields via an
                    # identity projection (we'll refine with pushdown later).
                    project_items.append(("*", LiteralValue(value="*", kind="string")))
                else:
                    project_items.append((self._item_name(item), self._lower_expr(item.expr)))
            plan = Project(items=project_items, input=plan)

        if stmt.distinct and not is_aggregate:
            plan = Distinct(input=plan)
        # DISTINCT with aggregates is unusual; we skip Distinct after Group.

        # ORDER BY: keys may reference outputs; they also reference original
        # expressions. We'll lower the expression as written (semantic analyzer
        # already validated correctness).
        if stmt.order_by:
            keys = []
            for order in stmt.order_by:
                keys.append((self._lower_expr(order.expr), order.descending))
            plan = Sort(keys=keys, input=plan)

        if stmt.offset is not None and hasattr(stmt.offset, "value"):
            plan = Skip(count=int(stmt.offset.value), input=plan)
        if stmt.limit is not None and hasattr(stmt.limit, "value"):
            plan = Limit(count=int(stmt.limit.value), input=plan)

        return plan

    def _build_base_scan(self, stmt: SelectStatement) -> LogicalPlan:
        if stmt.from_table is None:
            return Scan(collection="unknown", alias=None)
        coll = stmt.from_table.name
        alias = stmt.from_table.alias or coll
        return Scan(collection=coll, alias=alias)

    def _stmt_has_aggregates(self, stmt: SelectStatement) -> bool:
        for item in stmt.items:
            if self._expr_has_aggregate(item.expr):
                return True
        return False

    def _expr_has_aggregate(self, expr: Expr) -> bool:
        if isinstance(expr, FunctionCall) and _is_aggregate(expr.name):
            return True
        if isinstance(expr, BinaryOp):
            return self._expr_has_aggregate(expr.left) or self._expr_has_aggregate(expr.right)
        if isinstance(expr, UnaryOp):
            return self._expr_has_aggregate(expr.operand)
        return False

    def _extract_aggregates(self, expr: Expr, default_name: str) -> List[Tuple[str, FunctionExpr]]:
        result: List[Tuple[str, FunctionExpr]] = []
        if isinstance(expr, FunctionCall) and _is_aggregate(expr.name):
            ir_args = tuple(self._lower_expr(a) for a in expr.args)
            result.append(
                (
                    default_name,
                    FunctionExpr(
                        name=expr.name.upper(),
                        args=ir_args,
                        distinct=expr.distinct,
                        aggregate=True,
                        type=self._aggregate_type(expr.name, ir_args),
                    ),
                )
            )
            return result
        if isinstance(expr, BinaryOp):
            result.extend(self._extract_aggregates(expr.left, default_name + "_left"))
            result.extend(self._extract_aggregates(expr.right, default_name + "_right"))
        if isinstance(expr, UnaryOp):
            result.extend(self._extract_aggregates(expr.operand, default_name))
        return result

    def _aggregate_type(self, name: str, args: Tuple) -> FieldType:
        uname = name.upper()
        if uname == "COUNT":
            return FieldType.INTEGER
        if uname == "AVG":
            return FieldType.DECIMAL
        if uname in ("SUM", "MIN", "MAX") and args:
            return args[0].type
        return FieldType.ANY

    def _item_name(self, item: SelectItem) -> str:
        if item.alias:
            return item.alias
        if isinstance(item.expr, Identifier):
            return item.expr.name or (item.expr.parts[-1] if item.expr.parts else "expr")
        if isinstance(item.expr, FunctionCall):
            return f"{item.expr.name}"
        return "expr"

    def _build_group_projection(
        self, stmt: SelectStatement, group_node: Group
    ) -> List[Tuple[str, IRExpr]]:
        # Map each aggregate output name to its IR expression.
        agg_map: Dict[str, IRExpr] = {}
        for out_name, agg_expr in group_node.aggregates:
            agg_map[out_name] = agg_expr

        items: List[Tuple[str, IRExpr]] = []
        # Group keys are available as expressions; also produce them by name.
        used_keys: List[IRExpr] = list(group_node.group_keys)
        key_names = []
        for i, k in enumerate(used_keys):
            name = self._default_expr_name(k) or f"group_{i}"
            key_names.append(name)
            items.append((name, k))

        for i, item in enumerate(stmt.items):
            if isinstance(item.expr, FunctionCall) and _is_aggregate(item.expr.name):
                name = self._item_name(item)
                if name in agg_map:
                    items.append((name, agg_map[name]))
                else:
                    items.append((name, self._lower_expr(item.expr)))
                continue
            if isinstance(item.expr, BinaryOp) and self._expr_has_aggregate(item.expr):
                name = self._item_name(item) or f"agg_{i}"
                # Replace aggregate subexpressions with their grouped forms.
                lowered = self._replace_aggregates_in_expr(item.expr, agg_map)
                items.append((name, lowered))
                continue
            # A non-aggregate item in GROUP BY context must be a group key.
            items.append((self._item_name(item), self._lower_expr(item.expr)))
        return items

    def _replace_aggregates_in_expr(self, expr: Expr, agg_map: Dict[str, IRExpr]) -> IRExpr:
        if isinstance(expr, FunctionCall) and _is_aggregate(expr.name):
            key = self._item_name_from_call(expr)
            if key in agg_map:
                return agg_map[key]
        # structural clone
        from copy import deepcopy

        # For the subset, we don't build complex computed aggregates here.
        return self._lower_expr(expr)

    def _item_name_from_call(self, expr: FunctionCall) -> str:
        return expr.name.upper()

    def _default_expr_name(self, expr: IRExpr) -> Optional[str]:
        if hasattr(expr, "name") and isinstance(getattr(expr, "name"), str):
            return getattr(expr, "name")
        return None

    # -- Expression lowering ----------------------------------------------

    def _lower_expr(self, expr: Expr):
        if isinstance(expr, Literal):
            kind = expr.kind
            value = expr.value
            return LiteralValue(value=value, kind=kind, type=_literal_type(kind))
        if isinstance(expr, Identifier):
            return ColumnRef(
                name=expr.resolved_field or expr.name or (expr.parts[-1] if expr.parts else ""),
                source=None,
            )
        if isinstance(expr, UnaryOp):
            operand = self._lower_expr(expr.operand)
            if expr.op == "NOT":
                return UnaryExpr(op="NOT", operand=operand, type=FieldType.BOOLEAN)
            if expr.op == "-":
                return UnaryExpr(op="-", operand=operand, type=operand.type)
            return UnaryExpr(op=expr.op, operand=operand)
        if isinstance(expr, BinaryOp):
            return BinaryExpr(
                op=expr.op,
                left=self._lower_expr(expr.left),
                right=self._lower_expr(expr.right),
            )
        if isinstance(expr, FunctionCall):
            if _is_aggregate(expr.name):
                return FunctionExpr(
                    name=expr.name.upper(),
                    args=tuple(self._lower_expr(a) for a in expr.args),
                    distinct=expr.distinct,
                    aggregate=True,
                    type=self._aggregate_type(expr.name, tuple()),
                )
            args = tuple(self._lower_expr(a) for a in expr.args)
            return FunctionExpr(
                name=expr.name.upper(), args=args, distinct=False, aggregate=False
            )
        if isinstance(expr, (IsNullExpr,)):
            # AST IsNullExpr -> IRIsNullExpr
            from ..ast import IsNullExpr as AIN

            a = expr
            return IRIsNullExpr(
                operand=self._lower_expr(a.operand), negated=a.negated
            )
        # Fallback: boolean literal false
        return LiteralValue(value=False, kind="boolean", type=FieldType.BOOLEAN)

    def _lower_insert(self, stmt: InsertStatement) -> LogicalPlan:
        rows = []
        for row in stmt.rows:
            rows.append([self._lower_expr(v) for v in row])
        return Insert(collection=stmt.table.name, columns=stmt.columns, rows=rows)

    def _lower_update(self, stmt: UpdateStatement) -> LogicalPlan:
        assignments = []
        for a in stmt.assignments:
            assignments.append((a.field, self._lower_expr(a.value)))
        pred = self._lower_expr(stmt.where) if stmt.where is not None else None
        return Update(collection=stmt.table.name, assignments=assignments, predicate=pred)

    def _lower_delete(self, stmt: DeleteStatement) -> LogicalPlan:
        pred = self._lower_expr(stmt.where) if stmt.where is not None else None
        return Delete(collection=stmt.table.name, predicate=pred)


def _is_aggregate(name: str) -> bool:
    return name.upper() in _AGGREGATE_MAP


def _literal_type(kind: str) -> FieldType:
    return {
        "integer": FieldType.INTEGER,
        "decimal": FieldType.DECIMAL,
        "string": FieldType.STRING,
        "boolean": FieldType.BOOLEAN,
        "null": FieldType.NULL,
    }.get(kind, FieldType.ANY)


def lower(stmt) -> LogicalPlan:
    """Convenience wrapper."""
    return PlanLowerer().lower(stmt)
