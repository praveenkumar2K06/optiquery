"""Limit pushdown.

A ``Limit`` can be moved *below* a ``Project`` because projection is a
deterministic, row-wise transformation: it neither adds nor removes rows and
does not depend on how many rows are visible. Pushing the limit closer to the
source lets the backend stop reading earlier.

The pass deliberately does **not** push a limit below a ``Filter`` (which would
change which rows are returned), below a ``Sort`` (which must see all rows to
order them), below a ``Group``/``Distinct`` (which change cardinality), or below
a ``Skip``. Those would be incorrect.
"""
from __future__ import annotations

from ...ir import Limit, LogicalPlan, Project


class LimitPushdownPass:
    """Push LIMIT below projections where semantics are preserved."""

    name = "limit_pushdown"

    def __init__(self) -> None:
        self.changes = 0

    def apply(self, plan: LogicalPlan) -> LogicalPlan:
        self.changes = 0
        return self._rewrite(plan)

    def _rewrite(self, node: LogicalPlan) -> LogicalPlan:
        # Recurse into children first so nested patterns are handled.
        children = node.inputs()
        if children:
            node = node.with_inputs([self._rewrite(c) for c in children])

        # Limit(Project(x)) -> Project(Limit(x))
        if isinstance(node, Limit) and isinstance(node.input, Project):
            project = node.input
            pushed = Limit(count=node.count, input=project.input)
            self.changes += 1
            return Project(items=list(project.items), input=self._rewrite(pushed))

        return node


__all__ = ["LimitPushdownPass"]
