"""Projection pushdown pass: move projections closer to the data source.

This pass propagates the set of fields referenced by the whole plan down to the
Scan operator so the backend retrieves only the fields needed for filters,
projections, sorts, groups, and joins. Over-fetching a field is safe; the pass
never removes a field that a downstream operator needs.
"""
from __future__ import annotations

from typing import Set

from ...ir import (
    Filter,
    Group,
    LogicalPlan,
    Project,
    Scan,
    Sort,
)
from ...ir.transform import collect_referenced_fields


class ProjectionPushdownPass:
    """Push projections toward the source while preserving semantics."""

    name = "projection_pushdown"

    def __init__(self):
        self.changes = 0

    def apply(self, plan: LogicalPlan) -> LogicalPlan:
        self.changes = 0
        self._propagate(plan, set(), require_all=False)
        return plan

    def _propagate(
        self, node: LogicalPlan, required_from_above: Set[str], require_all: bool
    ) -> None:
        own: Set[str] = set()
        if isinstance(node, Project):
            for name, expr in node.items:
                if name == "*":
                    # SELECT * requires every field; pushdown must not narrow.
                    require_all = True
                    continue
                own |= collect_referenced_fields(expr)
        elif isinstance(node, Filter):
            own |= collect_referenced_fields(node.predicate)
        elif isinstance(node, Sort):
            for expr, _ in node.keys:
                own |= collect_referenced_fields(expr)
        elif isinstance(node, Group):
            for key in node.group_keys:
                own |= collect_referenced_fields(key)
            for _, agg in node.aggregates:
                own |= collect_referenced_fields(agg)

        needed = own | required_from_above

        for child in node.inputs():
            self._propagate(child, needed, require_all)

        if isinstance(node, Scan):
            if require_all:
                new_projection = None
            else:
                new_projection = sorted(needed)
            if node.projection != new_projection:
                node.projection = new_projection
                self.changes += 1
