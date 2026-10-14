"""Database-independent logical query plan operators.

A plan is a tree of :class:`LogicalPlan` nodes. Each node exposes ``inputs``
(its children) so generic passes can traverse and rewrite plans without knowing
every operator type.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple

from .expr import IRExpr


@dataclass
class LogicalPlan:
    """Base class for logical operators."""

    def inputs(self) -> List["LogicalPlan"]:
        """Return child operators (empty for leaves)."""
        return []

    def with_inputs(self, inputs: Sequence["LogicalPlan"]) -> "LogicalPlan":
        """Return a copy of this node with children replaced."""
        return self

    def node_name(self) -> str:
        return type(self).__name__


# ---------------------------------------------------------------------------
# Relational operators
# ---------------------------------------------------------------------------


@dataclass
class Scan(LogicalPlan):
    """A read of a collection, optionally with a field projection pushdown."""

    collection: str = ""
    alias: Optional[str] = None
    #: Fields the scan must physically read. ``None`` means "all fields".
    projection: Optional[List[str]] = None
    #: Optional statistics captured from metadata at planning time.
    estimated_cardinality: Optional[int] = None

    def node_name(self) -> str:
        return "Scan"


@dataclass
class Filter(LogicalPlan):
    """A predicate applied to its input."""

    predicate: IRExpr = None  # type: ignore[assignment]
    input: LogicalPlan = None  # type: ignore[assignment]

    def inputs(self) -> List[LogicalPlan]:
        return [self.input]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Filter":
        return replace(self, input=inputs[0])


@dataclass
class Project(LogicalPlan):
    """A projection of computed columns over its input."""

    #: (output_name, expression) pairs.
    items: List[Tuple[str, IRExpr]] = field(default_factory=list)
    input: LogicalPlan = None  # type: ignore[assignment]

    def inputs(self) -> List[LogicalPlan]:
        return [self.input]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Project":
        return replace(self, input=inputs[0])


@dataclass
class Sort(LogicalPlan):
    """An ORDER BY over its input."""

    #: (expression, descending) keys, applied left to right.
    keys: List[Tuple[IRExpr, bool]] = field(default_factory=list)
    input: LogicalPlan = None  # type: ignore[assignment]

    def inputs(self) -> List[LogicalPlan]:
        return [self.input]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Sort":
        return replace(self, input=inputs[0])


@dataclass
class Limit(LogicalPlan):
    """A row-count limit over its input."""

    count: int = 0
    input: LogicalPlan = None  # type: ignore[assignment]

    def inputs(self) -> List[LogicalPlan]:
        return [self.input]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Limit":
        return replace(self, input=inputs[0])


@dataclass
class Skip(LogicalPlan):
    """An OFFSET over its input."""

    count: int = 0
    input: LogicalPlan = None  # type: ignore[assignment]

    def inputs(self) -> List[LogicalPlan]:
        return [self.input]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Skip":
        return replace(self, input=inputs[0])


@dataclass
class Group(LogicalPlan):
    """A grouping/aggregation operator.

    ``group_keys`` are the GROUP BY expressions. ``aggregates`` is a list of
    (output_name, aggregate_expression) pairs. When ``aggregates`` is empty the
    node acts as a pure DISTINCT-style grouping.
    """

    group_keys: List[IRExpr] = field(default_factory=list)
    aggregates: List[Tuple[str, IRExpr]] = field(default_factory=list)
    input: LogicalPlan = None  # type: ignore[assignment]

    def inputs(self) -> List[LogicalPlan]:
        return [self.input]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Group":
        return replace(self, input=inputs[0])

    def node_name(self) -> str:
        return "Group"


@dataclass
class Distinct(LogicalPlan):
    """A DISTINCT over the columns produced by its input."""

    input: LogicalPlan = None  # type: ignore[assignment]

    def inputs(self) -> List[LogicalPlan]:
        return [self.input]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Distinct":
        return replace(self, input=inputs[0])


@dataclass
class Insert(LogicalPlan):
    """An INSERT into a collection."""

    collection: str = ""
    columns: List[str] = field(default_factory=list)
    rows: List[List[IRExpr]] = field(default_factory=list)
    input: Optional[LogicalPlan] = None

    def node_name(self) -> str:
        return "Insert"


@dataclass
class Update(LogicalPlan):
    """An UPDATE of documents in a collection."""

    collection: str = ""
    assignments: List[Tuple[str, IRExpr]] = field(default_factory=list)
    predicate: Optional[IRExpr] = None
    input: Optional[LogicalPlan] = None

    def node_name(self) -> str:
        return "Update"


@dataclass
class Delete(LogicalPlan):
    """A DELETE of documents in a collection."""

    collection: str = ""
    predicate: Optional[IRExpr] = None
    input: Optional[LogicalPlan] = None

    def node_name(self) -> str:
        return "Delete"


@dataclass
class Join(LogicalPlan):
    """A join between two inputs."""

    left: LogicalPlan = None  # type: ignore[assignment]
    right: LogicalPlan = None  # type: ignore[assignment]
    condition: Optional[IRExpr] = None
    join_type: str = "INNER"

    def inputs(self) -> List[LogicalPlan]:
        return [self.left, self.right]

    def with_inputs(self, inputs: Sequence[LogicalPlan]) -> "Join":
        return replace(self, left=inputs[0], right=inputs[1])

    def node_name(self) -> str:
        return "Join"
