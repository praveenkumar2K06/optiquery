"""Transforms a semantically-analyzed AST into a logical execution plan.

Lowering preserves SQL semantics:

* ``WHERE`` is applied before grouping,
* ``GROUP BY``/aggregates become a :class:`Group` node,
* ``HAVING`` becomes a :class:`Filter` over the grouped result,
* the projection is expressed as a :class:`Project` over the grouped or scanned
  result,
* ``ORDER BY`` for a plain ``SELECT`` is applied below the projection so sort
  keys remain visible even when they are not in the SELECT list,
* ``ORDER BY`` over aggregates is applied above the grouped projection whose
  output columns the keys reference,
* ``OFFSET``/``LIMIT`` become :class:`Skip`/:class:`Limit`.

Aggregate calls are collected once and replaced by references to the group's
output columns, so HAVING and ORDER BY can only reference computed aggregates
through those outputs.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..ast import (
    BinaryOp,
    CaseExpr as ASTCaseExpr,
    CaseWhen as ASTCaseWhen,
    DeleteStatement,
    Expr,
    FunctionCall,
    Identifier,
    InsertStatement,
    IsNullExpr as ASTIsNullExpr,
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
    Distinct,
    Delete,
    Filter,
    FunctionExpr,
    InListExpr,
    Insert,
    IRExpr,
    IsNullExpr as IRIsNullExpr,
    LikeExpr,
    Limit,
    LiteralValue,
    LogicalPlan,
    Project,
    Scan,
    Skip,
    Sort,
    UnaryExpr,
    Update,
    Group,
)
from ..metadata import FieldType

_AGGREGATE_NAMES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}

#: A sentinel marker used for SELECT * in a projection list.
STAR_SENTINEL = "*"


def _is_aggregate_name(name: str) -> bool:
    return name.upper() in _AGGREGATE_NAMES


class PlanLowerer:
    """Lowers AST statements to IR logical plans."""

    def __init__(self):
        self._agg_key_to_name: Dict[str, str] = {}
        self._agg_names_used: Dict[str, int] = {}

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
        from ..ir import Scan as _Scan

        return _Scan(collection="unknown")

    # -- SELECT ------------------------------------------------------------

    def _lower_select(self, stmt: SelectStatement) -> LogicalPlan:
        base: LogicalPlan = self._build_base_scan(stmt)

        if stmt.where is not None:
            base = Filter(predicate=self._lower_expr(stmt.where), input=base)

        is_aggregate = bool(stmt.group_by) or self._stmt_has_aggregates(stmt)

        if is_aggregate:
            # Aggregate output columns are only available after the Group (and
            # its projection), so ORDER BY stays above the projection here.
            plan = self._lower_aggregate_select(stmt, base)
            return self._wrap_sort_and_pagination(stmt, plan)

        project = self._lower_plain_select(stmt, base)

        if stmt.distinct:
            # SQL requires ORDER BY with DISTINCT to reference SELECT-list
            # columns, which the projection produces, so sorting after
            # DISTINCT/Project is correct.
            plan = Distinct(input=project)
            return self._wrap_sort_and_pagination(stmt, plan)

        # Plain SELECT: apply ORDER BY *below* the projection. This keeps the
        # sort keys visible even when they are not part of the SELECT list
        # (e.g. SELECT name FROM users ORDER BY age).
        order_keys = self._lower_plain_order_keys(stmt)
        if order_keys:
            plan: LogicalPlan = Project(
                items=project.items,
                input=Sort(keys=order_keys, input=project.input),
            )
        else:
            plan = project
        return self._wrap_pagination(stmt, plan)

    def _build_base_scan(self, stmt: SelectStatement) -> LogicalPlan:
        if stmt.from_table is None:
            return Scan(collection="unknown", alias=None)
        coll = stmt.from_table.name
        alias = stmt.from_table.alias or coll
        return Scan(collection=coll, alias=alias)

    def _lower_plain_select(
        self, stmt: SelectStatement, plan: LogicalPlan
    ) -> LogicalPlan:
        items: List[Tuple[str, IRExpr]] = []
        for item in stmt.items:
            if isinstance(item.expr, Star) or self._is_star_projection(item):
                items.append((STAR_SENTINEL, LiteralValue(value=STAR_SENTINEL, kind="string")))
            else:
                items.append((self._item_name(item), self._lower_expr(item.expr)))
        return Project(items=items, input=plan)

    def _is_star_projection(self, item: SelectItem) -> bool:
        return isinstance(item.expr, Star)

    def _lower_aggregate_select(
        self, stmt: SelectStatement, plan: LogicalPlan
    ) -> LogicalPlan:
        # Reset per-select aggregate bookkeeping.
        self._agg_key_to_name = {}
        self._agg_names_used = {}

        # 1. Register every aggregate call used anywhere in the statement.
        registered: List[Tuple[str, FunctionCall]] = []
        for item in stmt.items:
            for call in self._collect_aggregate_calls(item.expr):
                name = self._item_name(item) if self._is_bare_aggregate(item.expr) else None
                self._register_aggregate(call, name, registered)
        if stmt.having is not None:
            for call in self._collect_aggregate_calls(stmt.having):
                self._register_aggregate(call, None, registered)
        for order in stmt.order_by:
            for call in self._collect_aggregate_calls(order.expr):
                self._register_aggregate(call, None, registered)

        aggregates: List[Tuple[str, IRExpr]] = [
            (name, self._lower_aggregate_call(call)) for name, call in registered
        ]

        group_keys = [self._lower_expr(g) for g in stmt.group_by]
        group_node = Group(group_keys=group_keys, aggregates=aggregates, input=plan)
        result: LogicalPlan = group_node

        if stmt.having is not None:
            predicate = self._lower_expr_with_aggs(stmt.having)
            result = Filter(predicate=predicate, input=result)

        # 2. Projection over grouped output.
        items: List[Tuple[str, IRExpr]] = []
        for item in stmt.items:
            if isinstance(item.expr, Star):
                items.append((STAR_SENTINEL, LiteralValue(value=STAR_SENTINEL, kind="string")))
                continue
            if self._is_bare_aggregate(item.expr):
                out = self._agg_key_to_name[_ast_aggregate_key(item.expr)]
                items.append((self._item_name(item), ColumnRef(name=out, type=self._aggregate_type(item.expr.name, ()))))
                continue
            items.append((self._item_name(item), self._lower_expr_with_aggs(item.expr)))
        return Project(items=items, input=result)

    # -- Aggregate registration -------------------------------------------

    def _register_aggregate(
        self,
        call: FunctionCall,
        preferred_name: Optional[str],
        registered: List[Tuple[str, FunctionCall]],
    ) -> None:
        key = _ast_aggregate_key(call)
        if key in self._agg_key_to_name:
            return
        base = preferred_name or call.name.upper()
        name = base
        count = self._agg_names_used.get(base, 0)
        while name in self._agg_key_to_name.values():
            count += 1
            name = f"{base}_{count}"
        self._agg_names_used[base] = count
        self._agg_key_to_name[key] = name
        registered.append((name, call))

    def _lower_aggregate_call(self, call: FunctionCall) -> FunctionExpr:
        args = tuple(self._lower_expr(a) for a in call.args)
        return FunctionExpr(
            name=call.name.upper(),
            args=args,
            distinct=call.distinct,
            aggregate=True,
            type=self._aggregate_type(call.name, args),
        )

    def _aggregate_type(self, name: str, args: Tuple) -> FieldType:
        uname = name.upper()
        if uname == "COUNT":
            return FieldType.INTEGER
        if uname == "AVG":
            return FieldType.DECIMAL
        if uname in ("SUM", "MIN", "MAX") and args:
            return args[0].type
        return FieldType.ANY

    def _collect_aggregate_calls(self, expr: Optional[Expr]) -> List[FunctionCall]:
        result: List[FunctionCall] = []

        def visit(e: Expr):
            if isinstance(e, FunctionCall):
                if _is_aggregate_name(e.name):
                    result.append(e)
                    return
                for a in e.args:
                    visit(a)
            elif isinstance(e, BinaryOp):
                visit(e.left)
                visit(e.right)
            elif isinstance(e, UnaryOp):
                visit(e.operand)
            elif isinstance(e, ASTCaseExpr):
                if e.operand is not None:
                    visit(e.operand)
                for when in e.whens:
                    visit(when.condition)
                    visit(when.result)
                if e.default is not None:
                    visit(e.default)
            elif isinstance(e, ASTIsNullExpr):
                visit(e.operand)
            elif hasattr(e, "operand"):
                visit(getattr(e, "operand"))

        visit(expr)
        return result

    def _lower_expr_with_aggs(self, expr: Expr) -> IRExpr:
        """Lower an expression inside an aggregate query, replacing aggregate
        calls with references to the group output columns."""
        if isinstance(expr, FunctionCall) and _is_aggregate_name(expr.name):
            key = _ast_aggregate_key(expr)
            if key in self._agg_key_to_name:
                return ColumnRef(name=self._agg_key_to_name[key], type=self._aggregate_type(expr.name, ()))
            # Not registered (shouldn't happen after collection) - register now.
            registered: List[Tuple[str, FunctionCall]] = []
            self._register_aggregate(expr, None, registered)
            return ColumnRef(name=self._agg_key_to_name[key], type=self._aggregate_type(expr.name, ()))
        if isinstance(expr, BinaryOp):
            return BinaryExpr(
                op=expr.op,
                left=self._lower_expr_with_aggs(expr.left),
                right=self._lower_expr_with_aggs(expr.right),
            )
        if isinstance(expr, UnaryOp):
            return UnaryExpr(op=expr.op, operand=self._lower_expr_with_aggs(expr.operand))
        return self._lower_expr(expr)

    def _stmt_has_aggregates(self, stmt: SelectStatement) -> bool:
        for item in stmt.items:
            if self._collect_aggregate_calls(item.expr):
                return True
        if stmt.having is not None and self._collect_aggregate_calls(stmt.having):
            return True
        return False

    def _is_bare_aggregate(self, expr: Expr) -> bool:
        return isinstance(expr, FunctionCall) and _is_aggregate_name(expr.name)

    # -- Expression lowering ----------------------------------------------

    def _lower_expr(self, expr: Expr) -> IRExpr:
        if isinstance(expr, Literal):
            return LiteralValue(value=expr.value, kind=expr.kind, type=_literal_type(expr.kind))
        if isinstance(expr, Identifier):
            name = expr.resolved_field or expr.name or (expr.parts[-1] if expr.parts else "")
            return ColumnRef(name=name)
        if isinstance(expr, Star):
            return LiteralValue(value=None, kind="star")
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
            args = tuple(self._lower_expr(a) for a in expr.args)
            if _is_aggregate_name(expr.name):
                return FunctionExpr(
                    name=expr.name.upper(),
                    args=args,
                    distinct=expr.distinct,
                    aggregate=True,
                    type=self._aggregate_type(expr.name, args),
                )
            return FunctionExpr(name=expr.name.upper(), args=args, distinct=False, aggregate=False)
        if isinstance(expr, ASTIsNullExpr):
            return IRIsNullExpr(operand=self._lower_expr(expr.operand), negated=expr.negated)
        if isinstance(expr, ASTCaseExpr):
            whens = tuple(
                IRCaseWhen(
                    condition=self._lower_expr(w.condition),
                    result=self._lower_expr(w.result),
                )
                for w in expr.whens
            )
            default = self._lower_expr(expr.default) if expr.default is not None else None
            return IRCaseExpr(whens=whens, default=default)
        if isinstance(expr, ASTIsNullExpr):
            return IRIsNullExpr(operand=self._lower_expr(expr.operand), negated=expr.negated)
        # InList / Between / Like: reachable through the AST module classes.
        return self._lower_constraint(expr)

    def _lower_constraint(self, expr: Expr) -> IRExpr:
        from ..ast import BetweenExpr as ASTBetween, InListExpr as ASTInList, LikeExpr as ASTLike

        if isinstance(expr, ASTInList):
            return InListExpr(
                operand=self._lower_expr(expr.operand),
                options=tuple(self._lower_expr(o) for o in expr.options),
                negated=expr.negated,
            )
        if isinstance(expr, ASTBetween):
            return BetweenExpr(
                operand=self._lower_expr(expr.operand),
                low=self._lower_expr(expr.low),
                high=self._lower_expr(expr.high),
                negated=expr.negated,
            )
        if isinstance(expr, ASTLike):
            return LikeExpr(
                operand=self._lower_expr(expr.operand),
                pattern=self._lower_expr(expr.pattern),
                negated=expr.negated,
            )
        return LiteralValue(value=False, kind="boolean", type=FieldType.BOOLEAN)

    # -- ORDER BY / pagination --------------------------------------------

    def _wrap_sort_and_pagination(
        self, stmt: SelectStatement, plan: LogicalPlan
    ) -> LogicalPlan:
        if stmt.order_by:
            keys = []
            for order in stmt.order_by:
                expr = self._lower_order_expr(order.expr)
                keys.append((expr, order.descending))
            plan = Sort(keys=keys, input=plan)
        return self._wrap_pagination(stmt, plan)

    def _wrap_pagination(
        self, stmt: SelectStatement, plan: LogicalPlan
    ) -> LogicalPlan:
        if stmt.offset is not None and isinstance(stmt.offset, Literal):
            plan = Skip(count=int(stmt.offset.value), input=plan)
        if stmt.limit is not None and isinstance(stmt.limit, Literal):
            plan = Limit(count=int(stmt.limit.value), input=plan)
        return plan

    def _lower_plain_order_keys(
        self, stmt: SelectStatement
    ) -> List[Tuple[IRExpr, bool]]:
        """Lower ORDER BY keys against the pre-projection input.

        Select-list aliases are substituted with their underlying expressions so
        that ``ORDER BY alias`` still resolves when the sort sits below the
        projection.
        """
        aliases: Dict[str, Expr] = {}
        for item in stmt.items:
            if item.alias:
                aliases[item.alias.lower()] = item.expr

        keys: List[Tuple[IRExpr, bool]] = []
        for order in stmt.order_by:
            expr = order.expr
            if isinstance(expr, Identifier) and len(expr.parts) == 1:
                target = aliases.get(expr.parts[0].lower())
                if target is not None:
                    expr = target
            keys.append((self._lower_expr(expr), order.descending))
        return keys

    def _lower_order_expr(self, expr: Expr) -> IRExpr:
        if self._agg_key_to_name:
            return self._lower_expr_with_aggs(expr)
        return self._lower_expr(expr)

    # -- Names -------------------------------------------------------------

    def _item_name(self, item: SelectItem) -> str:
        if item.alias:
            return item.alias
        if isinstance(item.expr, Identifier):
            return item.expr.name or (item.expr.parts[-1] if item.expr.parts else "expr")
        if isinstance(item.expr, FunctionCall):
            return item.expr.name
        return "expr"

    # -- INSERT / UPDATE / DELETE -----------------------------------------

    def _lower_insert(self, stmt: InsertStatement) -> LogicalPlan:
        rows = [[self._lower_expr(v) for v in row] for row in stmt.rows]
        return Insert(collection=stmt.table.name, columns=stmt.columns, rows=rows)

    def _lower_update(self, stmt: UpdateStatement) -> LogicalPlan:
        assignments = [(a.field, self._lower_expr(a.value)) for a in stmt.assignments]
        pred = self._lower_expr(stmt.where) if stmt.where is not None else None
        return Update(collection=stmt.table.name, assignments=assignments, predicate=pred)

    def _lower_delete(self, stmt: DeleteStatement) -> LogicalPlan:
        pred = self._lower_expr(stmt.where) if stmt.where is not None else None
        return Delete(collection=stmt.table.name, predicate=pred)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _literal_type(kind: str) -> FieldType:
    return {
        "integer": FieldType.INTEGER,
        "decimal": FieldType.DECIMAL,
        "string": FieldType.STRING,
        "boolean": FieldType.BOOLEAN,
        "null": FieldType.NULL,
    }.get(kind, FieldType.ANY)


def _ast_aggregate_key(call: FunctionCall) -> str:
    """A structural key identifying an aggregate call within one SELECT."""
    parts = []
    for arg in call.args:
        parts.append(_ast_expr_key(arg))
    distinct = "DISTINCT " if call.distinct else ""
    return f"{call.name.upper()}({distinct}{', '.join(parts)})"


def _ast_expr_key(expr: Optional[Expr]) -> str:
    if expr is None:
        return "NULL"
    if isinstance(expr, Literal):
        if expr.kind == "string":
            return "'" + str(expr.value) + "'"
        return str(expr.value)
    if isinstance(expr, Identifier):
        return expr.qualified_name.lower() if hasattr(expr, "qualified_name") else expr.name.lower()
    if isinstance(expr, Star):
        return "*"
    if isinstance(expr, BinaryOp):
        return f"({_ast_expr_key(expr.left)} {expr.op} {_ast_expr_key(expr.right)})"
    if isinstance(expr, UnaryOp):
        return f"{expr.op}{_ast_expr_key(expr.operand)}"
    if isinstance(expr, FunctionCall):
        args = ", ".join(_ast_expr_key(a) for a in expr.args)
        return f"{expr.name.upper()}({args})"
    return repr(expr)


def lower(stmt) -> LogicalPlan:
    """Convenience wrapper."""
    return PlanLowerer().lower(stmt)
