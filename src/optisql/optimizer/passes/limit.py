"""Limit and sort optimization (push limit toward source where equivalent)."""

from __future__ import annotations

from ...ir import Limit, LogicalPlan, Sort
from ...ir.transform import clone_plan


class LimitPushdownPass:
    """Push LIMIT toward the backend when semantics are preserved."""

    name = "limit_pushdown"

    def __init__(self):
        self.changes = 0

    def apply(self, plan: LogicalPlan) -> LogicalPlan:
        self.changes = 0
        cloned = clone_plan(plan)
        return self._rewrite(cloned)

    def _rewrite(self, node: LogicalPlan) -> LogicalPlan:
        for child in node.inputs():
            self._rewrite(child)
        # Simplified: do not reorder across Sort in this subset to be safe.
        return node
