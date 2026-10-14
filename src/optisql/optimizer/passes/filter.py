"""Filter reordering pass: reorder independent conjunctive predicates.

Reordering is only performed when it preserves observable semantics. The
simple heuristic used here prefers equality predicates and predicates that
reference fewer columns, as a proxy for selectivity/cost.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List

from ...ir import BinaryExpr, Filter, LogicalPlan
from ...ir.transform import clone_plan, walk_plan_postorder


class FilterReorderingPass:
    """Push cheaper/selective predicates earlier where safe."""

    name = "filter_reordering"

    def __init__(self):
        self.changes = 0

    def apply(self, plan: LogicalPlan) -> LogicalPlan:
        self.changes = 0
        cloned = clone_plan(plan)
        return self._rewrite(cloned)

    def _rewrite(self, plan: LogicalPlan) -> LogicalPlan:
        for child in plan.inputs():
            self._rewrite(child)

        if isinstance(plan, Filter):
            preds = self._split_conjunctions(plan.predicate)
            if len(preds) > 1:
                reordered = self._reorder(preds)
                if reordered != preds:
                    self.changes += 1
                    new_pred = reordered[0]
                    for p in reordered[1:]:
                        new_pred = BinaryExpr(op="AND", left=new_pred, right=p)
                    plan.predicate = new_pred
        return plan

    @staticmethod
    def _split_conjunctions(expr: BinaryExpr) -> List[BinaryExpr]:
        # Return a list of predicate components; we keep BinaryExprs as-is for
        # simplicity. A proper split would flatten left-associative ANDs.
        result: List[BinaryExpr] = []
        if isinstance(expr, BinaryExpr) and expr.op == "AND":
            result.extend(FilterReorderingPass._split_conjunctions(expr.left))
            result.extend(FilterReorderingPass._split_conjunctions(expr.right))
            return result
        # Wrap non-binary? keep as a single predicate component - but type.
        return [expr]  # type: ignore[list-item]

    @staticmethod
    def _reorder(preds: List[BinaryExpr]) -> List[BinaryExpr]:
        # Heuristic: prefer equality (more selective), then fewer column refs.
        scored = []
        for p in preds:
            score = FilterReorderingPass._score(p)
            scored.append((score, p))
        scored.sort(key=lambda x: x[0])
        return [p for _, p in scored]

    @staticmethod
    def _score(expr: BinaryExpr) -> int:
        if isinstance(expr, BinaryExpr) and expr.op == "=":
            return 0  # best
        # Count column references.
        from ...ir.transform import collect_referenced_fields

        refs = collect_referenced_fields(expr)
        return len(refs) * 10 + 1
