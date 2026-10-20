"""Lightweight, explainable cost model for logical plans.

The model estimates the work a backend performs for a candidate plan. It is
deliberately simple and its outputs are *estimates*, never claims about measured
performance. Every component is recorded so a plan's cost can be explained.

Total estimated cost =
    scan cost + filter cost + traversal cost + sort cost
    + data movement cost + backend overhead

The formula is configurable through :class:`CostWeights` and uses schema
statistics when available, falling back to documented defaults otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from ..ir import (
    BinaryExpr,
    Filter,
    Group,
    IRExpr,
    Limit,
    LogicalPlan,
    Project,
    Scan,
    Skip,
    Sort,
)
from ..ir.transform import collect_referenced_fields
from ..metadata import Catalog


@dataclass
class CostWeights:
    """Tunable coefficients for the cost formula (documented defaults)."""

    #: Per-byte cost of reading documents during a scan.
    scan_per_kb: float = 0.001
    #: Per-document cost of evaluating a predicate.
    filter_per_doc: float = 0.0002
    #: Per-document cost of moving a document through the pipeline.
    move_per_doc: float = 0.00005
    #: Base cost of one backend round trip.
    backend_overhead: float = 0.01
    #: Multiplier applied to sort cost (comparisons + memory).
    sort_factor: float = 0.00002
    #: Per-document cost of grouping/hashing.
    group_per_doc: float = 0.0003
    #: Default row/doc width in bytes when statistics are missing.
    default_doc_size: int = 256
    #: Default collection cardinality when statistics are missing.
    default_cardinality: int = 1000
    #: Selectivity assumed for an indexed equality predicate.
    indexed_equality_selectivity: float = 0.01
    #: Selectivity assumed for a range predicate on an indexed field.
    indexed_range_selectivity: float = 0.1
    #: Selectivity assumed for an unindexed predicate.
    unindexed_selectivity: float = 0.5


@dataclass
class ComponentCost:
    """A single named component of the total estimated cost."""

    name: str
    value: float = 0.0
    notes: str = ""


@dataclass
class PlanCost:
    """Estimated cost for an entire query plan with a full breakdown."""

    total: float = 0.0
    components: Dict[str, ComponentCost] = field(default_factory=dict)
    #: True when at least one input statistic was missing/defaulted.
    approximate: bool = True
    notes: str = "Costs are estimates based on statistics or documented defaults; they do not reflect measured execution time."

    def add(self, name: str, value: float, notes: str = "") -> None:
        if name in self.components:
            self.components[name].value += value
            if notes:
                self.components[name].notes = notes
        else:
            self.components[name] = ComponentCost(name=name, value=value, notes=notes)
        self.total += value

    def breakdown(self) -> str:
        lines = [f"{c.name:<14} {c.value:.6f}  {c.notes}" for c in self.components.values()]
        lines.append(f"{'TOTAL':<14} {self.total:.6f}")
        return "\n".join(lines)


class CostModel:
    """Estimates the execution cost of a logical plan."""

    def __init__(
        self,
        catalog: Optional[Catalog] = None,
        weights: Optional[CostWeights] = None,
    ):
        self.catalog = catalog if catalog is not None else Catalog()
        self.weights = weights or CostWeights()

    def estimate(self, plan: LogicalPlan) -> PlanCost:
        cost = PlanCost()
        self._estimate_node(plan, cost, {"cardinality": None, "doc_size": None})
        if not cost.components:
            cost.add("backend_overhead", self.weights.backend_overhead, "Empty plan")
        else:
            cost.add(
                "backend_overhead",
                self.weights.backend_overhead,
                "One backend round trip",
            )
        return cost

    # -- helpers -----------------------------------------------------------

    def _estimate_node(self, node: LogicalPlan, cost: PlanCost, ctx: dict) -> dict:
        for child in node.inputs():
            ctx = self._estimate_node(child, cost, ctx)
        return self._node(node, cost, ctx)

    def _node(self, node: LogicalPlan, cost: PlanCost, ctx: dict) -> dict:
        w = self.weights

        if isinstance(node, Scan):
            return self._scan(node, cost, ctx)
        if isinstance(node, Filter):
            return self._filter(node, cost, ctx)
        if isinstance(node, Project):
            cost.add("project", w.move_per_doc * self._card(ctx), "Projection transform")
            return ctx
        if isinstance(node, Sort):
            card = self._card(ctx)
            # O(n log n) comparisons.
            import math

            comparisons = card * max(1.0, math.log2(max(2, card)))
            cost.add("sort", comparisons * w.sort_factor, f"Sort ~{int(comparisons)} comparisons")
            return ctx
        if isinstance(node, Group):
            card = self._card(ctx)
            cost.add("group", card * w.group_per_doc, "Hash grouping/aggregation")
            ctx = dict(ctx)
            ctx["cardinality"] = max(1, int(card * 0.3))
            return ctx
        if isinstance(node, (Limit, Skip)):
            count = getattr(node, "count", 0)
            if isinstance(node, Limit):
                ctx = dict(ctx)
                ctx["cardinality"] = min(self._card(ctx), count)
            return ctx

        cost.add("traversal", w.move_per_doc * self._card(ctx), f"{node.node_name()} traversal")
        return ctx

    def _scan(self, node: Scan, cost: PlanCost, ctx: dict) -> dict:
        w = self.weights
        schema = self.catalog.get_collection(node.collection) if node.collection else None
        card = schema.cardinality if schema and schema.cardinality else None
        doc_size = schema.avg_doc_size if schema and schema.avg_doc_size else None
        approximate = card is None or doc_size is None
        if card is None:
            card = w.default_cardinality
        if doc_size is None:
            doc_size = w.default_doc_size
        scan_cost = card * (doc_size / 1024.0) * w.scan_per_kb
        cost.add("scan", scan_cost, f"Scan '{node.collection}' card~{card} doc~{doc_size}B")
        if approximate:
            cost.approximate = True
        return {"cardinality": card, "doc_size": doc_size}

    def _filter(self, node: Filter, cost: PlanCost, ctx: dict) -> dict:
        w = self.weights
        card = self._card(ctx)
        selectivity, notes = self._predicate_selectivity(node.predicate, node)
        filter_cost = card * w.filter_per_doc
        cost.add("filter", filter_cost, f"Predicate ({notes}, sel~{selectivity:.2f})")
        ctx = dict(ctx)
        ctx["cardinality"] = max(1, int(card * selectivity))
        return ctx

    def _predicate_selectivity(
        self, predicate: Optional[IRExpr], node: Filter
    ) -> Tuple[float, str]:
        """Estimate selectivity and describe the reasoning."""
        w = self.weights
        if predicate is None:
            return 1.0, "no predicate"

        if isinstance(predicate, BinaryExpr) and predicate.op in ("AND", "OR"):
            left_sel, left_note = self._predicate_selectivity(predicate.left, node)
            right_sel, right_note = self._predicate_selectivity(predicate.right, node)
            if predicate.op == "AND":
                return left_sel * right_sel, f"AND({left_note} * {right_note})"
            return min(1.0, left_sel + right_sel), f"OR({left_note} + {right_note})"

        indexed = self._predicate_is_indexed(predicate, node)
        if isinstance(predicate, BinaryExpr) and predicate.op == "=":
            if indexed:
                return w.indexed_equality_selectivity, "indexed equality"
            return w.unindexed_selectivity * 0.5, "unindexed equality"
        if isinstance(predicate, BinaryExpr) and predicate.op in ("<", ">", "<=", ">="):
            if indexed:
                return w.indexed_range_selectivity, "indexed range"
            return w.unindexed_selectivity, "unindexed range"
        return w.unindexed_selectivity, "default"

    def _predicate_is_indexed(self, predicate: IRExpr, node: Filter) -> bool:
        # Find the source collection schema by walking down to a Scan.
        schema = None
        cur: Optional[LogicalPlan] = node.input
        while cur is not None:
            if isinstance(cur, Scan) and cur.collection:
                schema = self.catalog.get_collection(cur.collection)
                break
            children = cur.inputs()
            cur = children[0] if children else None
        if schema is None:
            return False
        fields = collect_referenced_fields(predicate)
        return bool(fields) and all(schema.get_field(f) and schema.get_field(f).indexed for f in fields)

    @staticmethod
    def _card(ctx: dict) -> float:
        value = ctx.get("cardinality")
        return float(value) if value else float(CostWeights().default_cardinality)
