"""Utilities for traversing and transforming IR plans."""
from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, List, Set, Tuple

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
    UnaryExpr,
)
from .plan import LogicalPlan


def walk_plan(plan: LogicalPlan, visitor):
    """Pre-order traversal: visit the node, then its inputs."""
    visitor(plan)
    for child in plan.inputs():
        walk_plan(child, visitor)


def walk_plan_postorder(plan: LogicalPlan, visitor):
    """Post-order traversal: visit children, then the node."""
    for child in plan.inputs():
        walk_plan_postorder(child, visitor)
    visitor(plan)


def plan_dependencies(plan: LogicalPlan) -> Dict[int, List[int]]:
    """Return an adjacency map (node-id -> child node-ids) for the plan tree."""
    deps: Dict[int, List[int]] = {}

    def record(node: LogicalPlan):
        deps[id(node)] = [id(c) for c in node.inputs()]

    walk_plan_postorder(plan, record)
    return deps


def collect_referenced_fields(expr: IRExpr) -> Set[str]:
    """Collect canonical field names referenced by an IR expression."""
    fields: Set[str] = set()

    def visit(e: IRExpr):
        if isinstance(e, ColumnRef):
            fields.add(e.name)
        elif isinstance(e, BinaryExpr):
            visit(e.left)
            visit(e.right)
        elif isinstance(e, UnaryExpr):
            visit(e.operand)
        elif isinstance(e, FunctionExpr):
            for a in e.args:
                visit(a)
        elif isinstance(e, InListExpr):
            visit(e.operand)
            for o in e.options:
                visit(o)
        elif isinstance(e, BetweenExpr):
            for term in (e.operand, e.low, e.high):
                visit(term)
        elif isinstance(e, LikeExpr):
            visit(e.operand)
            visit(e.pattern)
        elif isinstance(e, IsNullExpr):
            visit(e.operand)
        elif isinstance(e, CaseExpr):
            for when in e.whens:
                visit(when.condition)
                visit(when.result)
            if e.default is not None:
                visit(e.default)

    visit(expr)
    return fields


def clone_plan(plan: LogicalPlan) -> LogicalPlan:
    """Produce a deep copy of the plan tree by cloning children.

    This is a simple structural clone suitable for creating alternative plans.
    IR expression objects are immutable (frozen dataclasses), so they can be
    shared between clones.
    """
    return _clone_node(plan)


def _clone_node(node: LogicalPlan) -> LogicalPlan:
    if not node.inputs():
        return replace(node)
    # Build new children first (postorder) to preserve structure.
    cloned_inputs = [_clone_node(c) for c in node.inputs()]
    return node.with_inputs(cloned_inputs)
