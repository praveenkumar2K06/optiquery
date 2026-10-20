"""Database-independent intermediate representation for expressions.

IR expressions are normalized versions of AST expressions: field references
carry a canonical (unqualified) name plus an optional source alias, literals
carry an explicit logical type, and operators are canonical strings. Nothing in
this module references MongoDB or Redis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..metadata import FieldType


@dataclass(frozen=True)
class IRExpr:
    """Base class for all IR expression nodes.

    IR expressions are immutable and hashable so they can be shared in a DAG and
    compared structurally for common subexpression elimination. ``type`` and
    ``nullable`` are keyword-only so subclass fields (such as ``name``) keep
    natural positional order.
    """

    type: FieldType = field(default=FieldType.ANY, kw_only=True)
    #: Whether evaluating the expression can produce NULL.
    nullable: bool = field(default=True, kw_only=True)


@dataclass(frozen=True)
class ColumnRef(IRExpr):
    """A reference to a field, optionally qualified by a source alias."""

    name: str = ""
    source: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        return f"{self.source}.{self.name}" if self.source else self.name


@dataclass(frozen=True)
class LiteralValue(IRExpr):
    """A constant value with its logical type."""

    value: object = None
    kind: str = "null"


@dataclass(frozen=True)
class BinaryExpr(IRExpr):
    """A binary operation: arithmetic, comparison, or logical."""

    op: str = ""
    left: IRExpr = None  # type: ignore[assignment]
    right: IRExpr = None  # type: ignore[assignment]


@dataclass(frozen=True)
class UnaryExpr(IRExpr):
    """A unary operation such as NOT or numeric negation."""

    op: str = ""
    operand: IRExpr = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FunctionExpr(IRExpr):
    """A scalar or aggregate function call."""

    name: str = ""
    args: Tuple[IRExpr, ...] = ()
    distinct: bool = False
    #: True for COUNT/SUM/AVG/MIN/MAX; False for scalar functions.
    aggregate: bool = False


@dataclass(frozen=True)
class InListExpr(IRExpr):
    """``operand IN (options...)`` with an optional negation."""

    operand: IRExpr = None  # type: ignore[assignment]
    options: Tuple[IRExpr, ...] = ()
    negated: bool = False


@dataclass(frozen=True)
class BetweenExpr(IRExpr):
    """``operand BETWEEN low AND high`` with an optional negation."""

    operand: IRExpr = None  # type: ignore[assignment]
    low: IRExpr = None  # type: ignore[assignment]
    high: IRExpr = None  # type: ignore[assignment]
    negated: bool = False


@dataclass(frozen=True)
class LikeExpr(IRExpr):
    """``operand LIKE pattern`` with an optional negation."""

    operand: IRExpr = None  # type: ignore[assignment]
    pattern: IRExpr = None  # type: ignore[assignment]
    negated: bool = False


@dataclass(frozen=True)
class IsNullExpr(IRExpr):
    """``operand IS [NOT] NULL``."""

    operand: IRExpr = None  # type: ignore[assignment]
    negated: bool = False


@dataclass(frozen=True)
class CaseWhen:
    """A single WHEN/THEN branch of a case expression."""

    condition: IRExpr = None  # type: ignore[assignment]
    result: IRExpr = None  # type: ignore[assignment]


@dataclass(frozen=True)
class CaseExpr(IRExpr):
    """A CASE expression."""

    whens: Tuple[CaseWhen, ...] = ()
    default: Optional[IRExpr] = None


#: Operators considered deterministic and side-effect free (shareable in a DAG).
DETERMINISTIC_OPS = frozenset(
    {"+", "-", "*", "/", "%", "=", "!=", "<", ">", "<=", ">=", "AND", "OR", "NOT"}
)

#: Logical operators.
LOGICAL_OPS = frozenset({"AND", "OR"})
COMPARISON_OPS = frozenset({"=", "!=", "<", ">", "<=", ">="})
ARITHMETIC_OPS = frozenset({"+", "-", "*", "/", "%"})
