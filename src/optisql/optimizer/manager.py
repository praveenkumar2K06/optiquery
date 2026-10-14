"""Pass manager for executing a sequence of optimization passes."""
from __future__ import annotations

from typing import List, Optional, Type

from ..ir import LogicalPlan
from .passes.constant import ConstantFoldingPass
from .passes.cse import CommonSubexpressionEliminationPass
from .passes.filter import FilterReorderingPass
from .passes.limit import LimitPushdownPass
from .passes.projection import ProjectionPushdownPass


class PassManager:
    """Executes optimization passes in order."""

    def __init__(self, passes: Optional[List[object]] = None):
        self.passes = passes or self._default_passes()
        self.trace: List[dict] = []

    @staticmethod
    def _default_passes():
        return [
            ProjectionPushdownPass(),
            FilterReorderingPass(),
            ConstantFoldingPass(),
            LimitPushdownPass(),
            CommonSubexpressionEliminationPass(),
        ]

    def run(self, plan: LogicalPlan) -> LogicalPlan:
        current = plan
        self.trace = []
        for p in self.passes:
            prev = current
            current = p.apply(current)
            self.trace.append(
                {
                    "pass": getattr(p, "name", p.__class__.__name__),
                    "changes": getattr(p, "changes", 0),
                    "preserved": True,
                }
            )
        return current
