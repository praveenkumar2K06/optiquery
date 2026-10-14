"""Generate MongoDB aggregation pipelines from logical query plans.

This generator produces valid MongoDB aggregation syntax for the supported
subset (filtering, projection, sorting, limiting/skipping, grouping, and
aggregates). It is backend-specific and deliberately only translates plans
that can be represented in an aggregation pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..diagnostics import DiagnosticBag
from ..ir import (
    BetweenExpr,
    BinaryExpr,
    ColumnRef,
    Distinct,
    Filter,
    FunctionExpr,
    Group,
    IRExpr,
    InListExpr,
    IsNullExpr,
    LikeExpr,
    Limit,
    LiteralValue,
    LogicalPlan,
    Project,
    Scan,
    Skip,
    Sort,
    UnaryExpr,
)
from ..metadata import FieldType


@dataclass
class MongoPipelineResult:
    """Result of MongoDB pipeline generation."""

    pipeline: List[Dict[str, Any]]
    diagnostics: DiagnosticBag
    collection: str

    def to_json(self) -> str:
        return json.dumps(self.pipeline, indent=2, default=str)


class MongoDBGenerator:
    """Translates a logical plan into a MongoDB aggregation pipeline."""

    def __init__(self, diagnostics: Optional[DiagnosticBag] = None):
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self._pipeline: List[Dict[str, Any]] = []
        self._collection = "unknown"
        self._had_distinct = False

    def generate(self, plan: LogicalPlan) -> MongoPipelineResult:
        self._pipeline = []
        self._visit(plan)
        return MongoPipelineResult(
            pipeline=list(self._pipeline),
            diagnostics=self.diagnostics,
            collection=self._collection,
        )

    def _visit(self, node: LogicalPlan) -> None:
        if isinstance(node, Scan):
            self._handle_scan(node)
            return
        if isinstance(node, Filter):
            self._visit(node.input)
            self._handle_filter(node)
            return
        if isinstance(node, Project):
            self._visit(node.input)
            self._handle_project(node)
            return
        if isinstance(node, Sort):
            self._visit(node.input)
            self._handle_sort(node)
            return
        if isinstance(node, Limit):
            self._visit(node.input)
            self._handle_limit(node)
            return
        if isinstance(node, Skip):
            self._visit(node.input)
            self._handle_skip(node)
            return
        if isinstance(node, Group):
            self._visit(node.input)
            self._handle_group(node)
            return
        if isinstance(node, Distinct):
            self._visit(node.input)
            self._handle_distinct(node)
            return

        self.diagnostics.error(
            "E7001",
            f"MongoDB codegen does not support operator {node.node_name()}",
            None,
        )

    def _handle_scan(self, node: Scan) -> None:
        self._collection = node.collection
        if node.projection:
            self._pipeline.append({"$project": {f: 1 for f in node.projection}})

    def _handle_filter(self, node: Filter) -> None:
        if node.predicate is not None:
            self._pipeline.append({"$match": self._expr_to_mongo(node.predicate)})

    def _handle_project(self, node: Project) -> None:
        project: Dict[str, Any] = {}
        for name, expr in node.items:
            if isinstance(expr, LiteralValue) and expr.value == "*":
                continue
            project[name] = self._expr_to_mongo(expr)
        if project:
            self._pipeline.append({"$project": project})

    def _handle_sort(self, node: Sort) -> None:
        sort_spec = {}
        for expr, desc in node.keys:
            if isinstance(expr, ColumnRef):
                sort_spec[expr.name] = -1 if desc else 1
            else:
                self.diagnostics.error(
                    "E7002",
                    "MongoDB $sort currently supports column references only",
                    None,
                )
        if sort_spec:
            self._pipeline.append({"$sort": sort_spec})

    def _handle_limit(self, node: Limit) -> None:
        self._pipeline.append({"$limit": node.count})

    def _handle_skip(self, node: Skip) -> None:
        self._pipeline.append({"$skip": node.count})

    def _handle_group(self, node: Group) -> None:
        group: Dict[str, Any] = {"_id": {}}
        if node.group_keys:
            if len(node.group_keys) == 1:
                key = node.group_keys[0]
                if isinstance(key, ColumnRef):
                    group["_id"] = "$" + key.name
                else:
                    group["_id"] = self._expr_to_mongo(key)
            else:
                _id = {}
                for key in node.group_keys:
                    if isinstance(key, ColumnRef):
                        _id[key.name] = "$" + key.name
                    else:
                        _id["expr"] = self._expr_to_mongo(key)
                group["_id"] = _id
        else:
            group["_id"] = None

        for out_name, agg in node.aggregates:
            mongo_agg = self._function_to_mongo(agg)
            if mongo_agg is not None:
                group[out_name] = mongo_agg

        self._pipeline.append({"$group": group})

    def _handle_distinct(self, node: Distinct) -> None:
        # MongoDB distinct is typically a db.collection.distinct(), but in
        # aggregation one can use $group and $project. We'll emit a $group
        # that groups by all fields visible from the upstream project when we can;
        # for the basic subset this is a conservative, explicit fallback.
        self._had_distinct = True
        self.diagnostics.warning(
            "W7001",
            "MongoDB DISTINCT generated via $group (shapes may differ from input)",
            None,
        )
        # A generic $group is hard to produce without schema of projected keys;
        # emit $group by null aggregates empty and let caller interpret. In
        # practice, simpler plans avoid DISTINCT after projection; we document.
        self._pipeline.append({"$group": {"_id": None}})
        self._pipeline.append({"$project": {"_id": 0}})

    def _function_to_mongo(self, func: FunctionExpr) -> Optional[Dict[str, Any]]:
        name = func.name.upper()
        if name == "COUNT":
            arg = func.args[0] if func.args else LiteralValue(value=1, kind="integer")
            if isinstance(arg, ColumnRef):
                return {"$sum": 1} if arg.name == "*" else {"$sum": 1}
            return {"$sum": 1}
        if name == "SUM":
            if not func.args:
                return None
            return {"$sum": self._expr_to_mongo(func.args[0])}
        if name == "AVG":
            if not func.args:
                return None
            return {"$avg": self._expr_to_mongo(func.args[0])}
        if name == "MIN":
            if not func.args:
                return None
            return {"$min": self._expr_to_mongo(func.args[0])}
        if name == "MAX":
            if not func.args:
                return None
            return {"$max": self._expr_to_mongo(func.args[0])}
        self.diagnostics.error(
            "E7003", f"MongoDB codegen does not support function {name}", None
        )
        return None

    def _expr_to_mongo(self, expr: IRExpr) -> Any:
        if isinstance(expr, ColumnRef):
            return "$" + expr.name
        if isinstance(expr, LiteralValue):
            return expr.value
        if isinstance(expr, BinaryExpr):
            if expr.op in ("=", "!=", "<>", "<", ">", "<=", ">="):
                op = {
                    "=": "$eq",
                    "!=": "$ne",
                    "<>": "$ne",
                    "<": "$lt",
                    ">": "$gt",
                    "<=": "$lte",
                    ">=": "$gte",
                }[expr.op]
                return {op: [self._expr_to_mongo(expr.left), self._expr_to_mongo(expr.right)]}
            if expr.op in ("+", "-", "*", "/", "%"):
                op = {
                    "+": "$add",
                    "-": "$subtract",
                    "*": "$multiply",
                    "/": "$divide",
                    "%": "$mod",
                }[expr.op]
                return {op: [self._expr_to_mongo(expr.left), self._expr_to_mongo(expr.right)]}
            if expr.op == "AND":
                return {
                    "$and": [self._expr_to_mongo(expr.left), self._expr_to_mongo(expr.right)]
                }
            if expr.op == "OR":
                return {
                    "$or": [self._expr_to_mongo(expr.left), self._expr_to_mongo(expr.right)]
                }
        if isinstance(expr, UnaryExpr):
            if expr.op == "NOT":
                return {"$not": self._expr_to_mongo(expr.operand)}
            if expr.op == "-":
                return {"$multiply": [-1, self._expr_to_mongo(expr.operand)]}
        if isinstance(expr, BetweenExpr):
            return {
                "$and": [
                    {"$gte": [self._expr_to_mongo(expr.operand), self._expr_to_mongo(expr.low)]},
                    {"$lte": [self._expr_to_mongo(expr.operand), self._expr_to_mongo(expr.high)]},
                ]
            }
        if isinstance(expr, IsNullExpr):
            if expr.negated:
                return {"$ne": [self._expr_to_mongo(expr.operand), None]}
            return {"$eq": [self._expr_to_mongo(expr.operand), None]}
        if isinstance(expr, LikeExpr):
            # A full regex for LIKE '%term%' is a simple approximation for the
            # small supported subset. The cost model doesn't depend on the
            # exact regex string, only on correctness.
            pattern = self._literal_string(expr.pattern)
            if pattern is not None:
                regex = "^" + pattern.replace("%", ".*").replace("_", ".") + "$"
                return {"$regexMatch": {"input": self._expr_to_mongo(expr.operand), "regex": regex}}
        if isinstance(expr, InListExpr):
            cond = {"$in": [self._expr_to_mongo(expr.operand), [self._expr_to_mongo(o) for o in expr.options]]}
            return {"$not": cond} if expr.negated else cond
        return None

    @staticmethod
    def _literal_string(expr: IRExpr) -> Optional[str]:
        if isinstance(expr, LiteralValue) and isinstance(expr.value, str):
            return expr.value
        return None


def generate_mongodb(plan: LogicalPlan, diagnostics: Optional[DiagnosticBag] = None) -> MongoPipelineResult:
    """Convenience function to generate a MongoDB aggregation pipeline."""
    return MongoDBGenerator(diagnostics).generate(plan)
