"""Lightweight cost-based query optimizer.

The optimizer runs rule-based passes and then compares candidate equivalent
plans using the :class:`CostModel`. Candidate generation is deliberately small
and safe: it only produces plans that are known to be semantically equivalent
(e.g., different orderings of independent conjunctive predicates).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..diagnostics import DiagnosticBag
from ..ir import LogicalPlan
from ..metadata import Catalog
from .cost import CostModel, PlanCost
from .manager import PassManager
from .passes.filter import FilterReorderingPass


@dataclass
class CandidatePlan:
    """A candidate plan with its estimated cost and rationale."""

    plan: LogicalPlan
    cost: PlanCost
    rationale: str
    rule: str = "original"


@dataclass
class OptimizationResult:
    """Result of optimization."""

    original: LogicalPlan
    optimized: LogicalPlan
    candidates: List[CandidatePlan] = field(default_factory=list)
    selected: Optional[LogicalPlan] = None
    cost_breakdown: Optional[PlanCost] = None
    trace: List[dict] = field(default_factory=list)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    selection_rationale: str = ""

    def __post_init__(self):
        if self.selected is None:
            self.selected = self.optimized


class QueryOptimizer:
    """Applies rule-based passes and selects the lowest-cost candidate."""

    def __init__(
        self,
        catalog: Optional[Catalog] = None,
        cost_model: Optional[CostModel] = None,
        enable_passes: bool = True,
        enable_cost_planning: bool = True,
    ):
        self.catalog = catalog if catalog is not None else Catalog()
        self.cost_model = cost_model or CostModel(catalog=self.catalog)
        self.enable_passes = enable_passes
        self.enable_cost_planning = enable_cost_planning
        self.pass_manager = PassManager()

    def optimize(self, plan: LogicalPlan) -> OptimizationResult:
        original = plan

        # Phase A: rule-based rewriting (projection pushdown, filter
        # reordering, constant folding, CSE, limit pushdown).
        if self.enable_passes:
            optimized = self.pass_manager.run(plan)
            trace = self.pass_manager.trace
        else:
            optimized = plan
            trace = []

        # Phase B: cost-based candidate generation and selection.
        candidates: List[CandidatePlan] = []
        if self.enable_cost_planning:
            candidates = self._generate_candidates(original, optimized)

        selected = optimized
        selection_rationale = "Rule-based optimized plan (cost planning disabled)."
        selected_cost: Optional[PlanCost] = None

        if candidates:
            best = min(candidates, key=lambda c: c.cost.total)
            selected = best.plan
            selected_cost = best.cost
            selection_rationale = (
                f"Selected '{best.rule}' candidate with estimated cost "
                f"{best.cost.total:.6f} out of {len(candidates)} candidates."
            )
        else:
            selected_cost = self.cost_model.estimate(selected)

        return OptimizationResult(
            original=original,
            optimized=selected,
            candidates=candidates,
            selected=selected,
            cost_breakdown=selected_cost,
            trace=trace,
            selection_rationale=selection_rationale,
        )

    # -- Candidate generation ---------------------------------------------

    def _generate_candidates(
        self, original: LogicalPlan, optimized: LogicalPlan
    ) -> List[CandidatePlan]:
        candidates: List[CandidatePlan] = []

        base_cost = self.cost_model.estimate(optimized)
        candidates.append(
            CandidatePlan(
                plan=optimized,
                cost=base_cost,
                rationale="Rule-based optimized plan.",
                rule="optimized",
            )
        )

        # Alternative: try the opposite filter ordering where an AND predicate
        # has more than one conjunct. This is semantically equivalent for
        # deterministic, side-effect-free predicates.
        alt = self._alternative_filter_order(optimized)
        if alt is not None:
            alt_cost = self.cost_model.estimate(alt)
            candidates.append(
                CandidatePlan(
                    plan=alt,
                    cost=alt_cost,
                    rationale="Filter conjuncts reordered by descending selectivity.",
                    rule="filter_reorder_alt",
                )
            )

        # Alternative: the unoptimized plan, for comparison in explanations.
        if original is not optimized:
            orig_cost = self.cost_model.estimate(original)
            candidates.append(
                CandidatePlan(
                    plan=original,
                    cost=orig_cost,
                    rationale="Original logical plan before rule passes.",
                    rule="original",
                )
            )

        return candidates

    def _alternative_filter_order(self, plan: LogicalPlan) -> Optional[LogicalPlan]:
        """Produce a plan with filter conjuncts reversed, if applicable."""
        from ..ir import BinaryExpr, Filter
        from ..ir.transform import clone_plan, walk_plan_postorder

        reordered: List[LogicalPlan] = []
        found = False

        cloned = clone_plan(plan)

        def visit(node: LogicalPlan):
            nonlocal found
            if isinstance(node, Filter) and isinstance(node.predicate, BinaryExpr):
                if node.predicate.op == "AND":
                    left = node.predicate.left
                    right = node.predicate.right
                    node.predicate = BinaryExpr(op="AND", left=right, right=left)
                    found = True

        walk_plan_postorder(cloned, visit)
        return cloned if found else None
