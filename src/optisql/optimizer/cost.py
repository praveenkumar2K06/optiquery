"""Lightweight cost model for estimating plan execution costs.

Estimates are approximate and clearly marked as such. The model is
backend-agnostic and uses schema statistics when available, falling back to
documented defaults otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from ..ir import (
    Filter,
    Group,
    Limit,
    LogicalPlan,
    Project,
    Scan,
    Sort,
)
from ..ir.transform import collect_referenced_fields
from ..metadata import Catalog


@dataclass
class ComponentCost:
    """Breakdown of a single cost component."""

    name: str
    value: float = 0.0
    notes: str = ""


@dataclass
class PlanCost:
    """Estimated cost for an entire query plan."""

    total: float = 0.0
    components: Dict[str, ComponentCost] = field(default_factory=dict)
    approximate: bool = True
    notes: str = "Costs are estimated and may not reflect actual execution time."

    def add(self, name: str, value: float, notes: str = "") -> None:
        comp = ComponentCost(name=name, value=value, notes=notes)
        self.components[name] = comp
        self.total += value


class CostModel:
    """Estimates execution cost of logical plans."""

    DEFAULT_CARDINALITY = 1000
    DEFAULT_DOC_SIZE = 256
    DEFAULT_INDEX_SELECTIVITY = 0.1
    DEFAULT_UNINDEXED_SELECTIVITY = 0.5

    def __init__(self, catalog: Optional[Catalog] = None):
        self.catalog = catalog if catalog is not None else Catalog()

    def estimate(self, plan: LogicalPlan) -> PlanCost:
        cost = PlanCost()
        self._estimate_node(plan, cost, {})
        return cost

    def _estimate_node(
        self,
        node: LogicalPlan,
        cost: PlanCost,
        context: dict,
    ) -> dict:
        for child in node.inputs():
            context = self._estimate_node(child, cost, context)

        if isinstance(node, Scan):
            coll = node.collection
            schema = self.catalog.get_collection(coll) if coll else None
            card = schema.cardinality if schema and schema.cardinality else self.DEFAULT_CARDINALITY
            doc_size = schema.avg_doc_size if schema and schema.avg_doc_size else self.DEFAULT_DOC_SIZE
            scan_cost = card * (doc_size / 1024.0) * 0.001
            cost.add("scan", scan_cost, f"Scan {coll} (card ~{card})")
            context["cardinality"] = card
            context["doc_size"] = doc_size
            return context

        if isinstance(node, Filter):
            card = context.get("cardinality", self.DEFAULT_CARDINALITY)
            sel = self.DEFAULT_UNINDEXED_SELECTIVITY
            filter_cost = card * 0.0001
            cost.add("filter", filter_cost, f"Predicate evaluation (~{sel:.2f} selectivity)")
            context["cardinality"] = card * sel
            return context

        if isinstance(node, Project):
            cost.add("project", 0.0001, "Projection transformation")
            return context

        if isinstance(node, Sort):
            card = context.get("cardinality", self.DEFAULT_CARDINALITY)
            sort_cost = card * max(1, card / 1000.0) * 0.00001
            cost.add("sort", sort_cost, "Sort cost (O(n log n))")
            return context

        if isinstance(node, Group):
            card = context.get("cardinality", self.DEFAULT_CARDINALITY)
            group_cost = card * 0.0002
            cost.add("group", group_cost, "Grouping/aggregation")
            context["cardinality"] = max(1, card * 0.3)
            return context

        if isinstance(node, Limit):
            card = context.get("cardinality", self.DEFAULT_CARDINALITY)
            context["cardinality"] = min(card, node.count) if hasattr(node, "count") else card
            cost.add("limit", 0.000001, "Limit operation")
            return context

        cost.add("traversal", 0.000001, f"Traversal for {node.node_name()}")
        return context
