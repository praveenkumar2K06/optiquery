"""Lightweight cost-based optimizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..diagnostics import DiagnosticBag
from ..ir import LogicalPlan
from ..metadata import Catalog
from .cost import CostModel, PlanCost
from .manager import PassManager


@dataclass
class OptimizationResult:
    """Result of optimization."""

    original: LogicalPlan
    optimized: LogicalPlan
    candidates: List[tuple] = field(default_factory=list)  # (plan, cost, rationale)
    selected: LogicalPlan = None  # type: ignore[assignment]
    cost_breakdown: Optional[PlanCost] = None
    trace: List[dict] = field(default_factory=list)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)

    def __post_init__(self):
        if self.selected is None:
            self.selected = self.optimized


class QueryOptimizer:
    """Applies rule-based passes and estimates cost."""

    def __init__(
        self,
        catalog: Optional[Catalog] = None,
        cost_model: Optional[CostModel] = None,
        enable_passes: bool = True,
    ):
        self.catalog = catalog if catalog is not None else Catalog()
        self.cost_model = cost_model if cost_model is not None else CostModel(catalog=self.catalog)
        self.enable_passes = enable_passes
        self.pass_manager = PassManager()

    def optimize(self, plan: LogicalPlan) -> OptimizationResult:
        original = plan
        if self.enable_passes:
            optimized = self.pass_manager.run(plan)
            trace = self.pass_manager.trace
        else:
            optimized = plan
            trace = []

        cost = self.cost_model.estimate(optimized)
        return OptimizationResult(
            original=original,
            optimized=optimized,
            selected=optimized,
            cost_breakdown=cost,
            trace=trace,
        )
