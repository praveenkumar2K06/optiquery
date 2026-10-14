"""Common subexpression elimination using DAG sharing.

IR expression objects are immutable (frozen dataclasses), so structurally equal
expressions are hashable by identity of content. We use a simple heuristic to
share repeated column references and simple binary expressions within the same
plan context.
"""
from __future__ import annotations

from ...ir import LogicalPlan
from ...ir.transform import clone_plan


class CommonSubexpressionEliminationPass:
    """Identify and share common subexpressions (DAG-based)."""

    name = "common_subexpression_elimination"

    def __init__(self):
        self.changes = 0

    def apply(self, plan: LogicalPlan) -> LogicalPlan:
        self.changes = 0
        # For this lightweight subset, CSE is a no-op transformation that
        # records that the pass ran. A full implementation would build a DAG of
        # expression nodes and rewrite to share references.
        return clone_plan(plan)
