"""Typed AST node classes produced by the parser.

The AST preserves the structure and meaning of the original SQL. Nodes carry an
optional :class:`SourceSpan` so later phases can point at the construct that
caused a problem.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union

from ..diagnostics import SourceSpan


@dataclass
class Node:
    """Base class for every AST node."""

    span: Optional[SourceSpan] = field(default=None, kw_only=True)


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass
class Expr(Node):
    """Base class for expression nodes."""


@dataclass
class Literal(Expr):
    """A literal value: integer, decimal, string, boolean, or NULL."""

    value: object = None
    #: Literal kind: 'integer', 'decimal', 'string', 'boolean', 'null'.
    kind: str = "null"


@dataclass
class Identifier(Expr):
    """A possibly-qualified column reference, e.g. ``name`` or ``u.name``."""

    parts: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.parts[-1] if self.parts else ""

    @property
    def qualifier(self) -> Optional[str]:
        if len(self.parts) > 1:
            return ".".join(self.parts[:-1])
        return None

    @property
    def qualified_name(self) -> str:
        return ".".join(self.parts)


@dataclass
class QualifiedIdentifier(Identifier):
    """Alias kept for API compatibility; behaves like :class:`Identifier`."""


@dataclass
class Star(Expr):
    """``*`` or ``alias.*`` in a projection list."""

    qualifier: Optional[str] = None


@dataclass
class BinaryOp(Expr):
    """A binary arithmetic, comparison, or logical operation."""

    op: str = ""
    left: Expr = None  # type: ignore[assignment]
    right: Expr = None  # type: ignore[assignment]


@dataclass
class UnaryOp(Expr):
    """A unary operation such as ``NOT x`` or ``-x``."""

    op: str = ""
    operand: Expr = None  # type: ignore[assignment]


@dataclass
class FunctionCall(Expr):
    """A function invocation, e.g. ``COUNT(*)`` or ``SUM(age)``."""

    name: str = ""
    args: List[Expr] = field(default_factory=list)
    distinct: bool = False

    @property
    def is_aggregate(self) -> bool:
        from ..metadata import AGGREGATE_FUNCTIONS

        return self.name.upper() in AGGREGATE_FUNCTIONS


@dataclass
class BetweenExpr(Expr):
    """``expr BETWEEN low AND high``."""

    operand: Expr = None  # type: ignore[assignment]
    low: Expr = None  # type: ignore[assignment]
    high: Expr = None  # type: ignore[assignment]
    negated: bool = False


@dataclass
class InListExpr(Expr):
    """``expr IN (a, b, c)``."""

    operand: Expr = None  # type: ignore[assignment]
    options: List[Expr] = field(default_factory=list)
    negated: bool = False


@dataclass
class LikeExpr(Expr):
    """``expr LIKE pattern``."""

    operand: Expr = None  # type: ignore[assignment]
    pattern: Expr = None  # type: ignore[assignment]
    negated: bool = False


@dataclass
class IsNullExpr(Expr):
    """``expr IS [NOT] NULL``."""

    operand: Expr = None  # type: ignore[assignment]
    negated: bool = False


@dataclass
class CaseExpr(Expr):
    """``CASE [operand] WHEN ... THEN ... [ELSE ...] END``."""

    operand: Optional[Expr] = None
    whens: List[tuple] = field(default_factory=list)  # list of (condition, result)
    default: Optional[Expr] = None


# ---------------------------------------------------------------------------
# Clauses
# ---------------------------------------------------------------------------


@dataclass
class SelectItem(Node):
    """One item in the SELECT projection list."""

    expr: Expr = None  # type: ignore[assignment]
    alias: Optional[str] = None


@dataclass
class OrderByItem(Node):
    """A single ORDER BY term with a direction."""

    expr: Expr = None  # type: ignore[assignment]
    descending: bool = False


@dataclass
class TableRef(Node):
    """A table or collection reference, optionally aliased."""

    name: str = ""
    alias: Optional[str] = None


@dataclass
class JoinClause(Node):
    """A JOIN between two table references."""

    join_type: str = "INNER"  # INNER | LEFT | RIGHT
    table: TableRef = None  # type: ignore[assignment]
    condition: Optional[Expr] = None


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class Statement(Node):
    """Base class for statement nodes."""


@dataclass
class SelectStatement(Statement):
    """A SELECT query."""

    items: List[SelectItem] = field(default_factory=list)
    from_table: Optional[TableRef] = None
    joins: List[JoinClause] = field(default_factory=list)
    where: Optional[Expr] = None
    group_by: List[Expr] = field(default_factory=list)
    having: Optional[Expr] = None
    order_by: List[OrderByItem] = field(default_factory=list)
    limit: Optional[Expr] = None
    offset: Optional[Expr] = None
    distinct: bool = False

    @property
    def has_aggregates(self) -> bool:
        for item in self.items:
            if _contains_aggregate(item.expr):
                return True
        if self.having is not None and _contains_aggregate(self.having):
            return True
        return False


@dataclass
class Assignment(Node):
    """A ``SET field = value`` assignment in an UPDATE."""

    field: str = ""
    value: Expr = None  # type: ignore[assignment]


@dataclass
class InsertStatement(Statement):
    """An INSERT statement."""

    table: TableRef = None  # type: ignore[assignment]
    columns: List[str] = field(default_factory=list)
    rows: List[List[Expr]] = field(default_factory=list)


@dataclass
class UpdateStatement(Statement):
    """An UPDATE statement."""

    table: TableRef = None  # type: ignore[assignment]
    assignments: List[Assignment] = field(default_factory=list)
    where: Optional[Expr] = None


@dataclass
class DeleteStatement(Statement):
    """A DELETE statement."""

    table: TableRef = None  # type: ignore[assignment]
    where: Optional[Expr] = None


@dataclass
class ColumnDef(Node):
    """A column definition in CREATE TABLE."""

    name: str = ""
    type_name: str = ""


@dataclass
class CreateTableStatement(Statement):
    """A CREATE TABLE statement describing a collection schema."""

    table: TableRef = None  # type: ignore[assignment]
    columns: List[ColumnDef] = field(default_factory=list)


def _contains_aggregate(expr: Optional[Expr]) -> bool:
    """Recursively test whether an expression contains an aggregate call."""
    if expr is None:
        return False
    if isinstance(expr, FunctionCall):
        if expr.is_aggregate:
            return True
        return any(_contains_aggregate(a) for a in expr.args)
    if isinstance(expr, BinaryOp):
        return _contains_aggregate(expr.left) or _contains_aggregate(expr.right)
    if isinstance(expr, UnaryOp):
        return _contains_aggregate(expr.operand)
    if isinstance(expr, BetweenExpr):
        return (
            _contains_aggregate(expr.operand)
            or _contains_aggregate(expr.low)
            or _contains_aggregate(expr.high)
        )
    if isinstance(expr, InListExpr):
        return _contains_aggregate(expr.operand) or any(
            _contains_aggregate(o) for o in expr.options
        )
    if isinstance(expr, LikeExpr):
        return _contains_aggregate(expr.operand) or _contains_aggregate(expr.pattern)
    if isinstance(expr, IsNullExpr):
        return _contains_aggregate(expr.operand)
    if isinstance(expr, CaseExpr):
        parts = list(expr.whens) + (
            [(None, expr.default)] if expr.default is not None else []
        )
        result = False
        if expr.operand is not None:
            result = result or _contains_aggregate(expr.operand)
        for cond, val in parts:
            result = result or _contains_aggregate(cond) or _contains_aggregate(val)
        return result
    return False


#: Union alias used in type hints throughout the compiler.
AnyStatement = Union[
    SelectStatement,
    InsertStatement,
    UpdateStatement,
    DeleteStatement,
    CreateTableStatement,
]
