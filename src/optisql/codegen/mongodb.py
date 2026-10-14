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
    CaseExpr,
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
        self._scan_projection: Optional[List[str]] = None
        self._saw_project = False
        self._failed = False
        self._visit(plan)
        if self._failed:
            # A stage could not be translated. Return no pipeline rather than a
            # query that would silently return wrong or empty results.
            self._pipeline = []
        else:
            self._insert_scan_projection()
        return MongoPipelineResult(
            pipeline=list(self._pipeline),
            diagnostics=self.diagnostics,
            collection=self._collection,
        )

    def _insert_scan_projection(self) -> None:
        """Insert an early field-limiting $project right after leading $match.

        MongoDB evaluates $match best when it is first (index usage), so rather
        than emitting a projection at the very start we place it immediately
        after any leading $match stages. We skip it entirely when a downstream
        $project already limits the fields.
        """
        if not self._scan_projection or self._saw_project:
            return
        insert_at = 0
        while insert_at < len(self._pipeline) and "$match" in self._pipeline[insert_at]:
            insert_at += 1
        stage = {"$project": {f: 1 for f in self._scan_projection}}
        if insert_at < len(self._pipeline) and "$project" in self._pipeline[insert_at]:
            return
        self._pipeline.insert(insert_at, stage)

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
            self._scan_projection = list(node.projection)

    def _handle_filter(self, node: Filter) -> None:
        if node.predicate is not None:
            self._pipeline.append({"$match": self._predicate_to_query(node.predicate)})

    # -- $match query-document generation ---------------------------------
    #
    # A $match stage expects a MongoDB *query document* (the same grammar used
    # by find()), not an aggregation expression. Field comparisons therefore
    # use the ``{field: {$op: value}}`` form, and field-to-field comparisons are
    # wrapped in $expr so the aggregation-expression form is valid.

    def _predicate_to_query(self, expr: IRExpr) -> Dict[str, Any]:
        if isinstance(expr, BinaryExpr) and expr.op in ("AND", "OR"):
            op = "$and" if expr.op == "AND" else "$or"
            return {op: [self._predicate_to_query(expr.left), self._predicate_to_query(expr.right)]}

        if isinstance(expr, UnaryExpr) and expr.op == "NOT":
            # $nor with a single clause negates the inner predicate safely.
            return {"$nor": [self._predicate_to_query(expr.operand)]}

        if isinstance(expr, BinaryExpr) and expr.op in ("=", "!=", "<", ">", "<=", ">="):
            left, right = expr.left, expr.right
            if isinstance(left, ColumnRef) and not isinstance(right, ColumnRef):
                rhs = self._expr_to_mongo(right)
                if expr.op == "=" and not isinstance(right, (FunctionExpr,)):
                    return {left.name: rhs}
                op = {
                    "=": "$eq", "!=": "$ne", "<": "$lt", ">": "$gt",
                    "<=": "$lte", ">=": "$gte",
                }[expr.op]
                return {left.name: {op: rhs}}
            # Field-to-field or computed comparison requires $expr.
            return {"$expr": self._expr_to_mongo(expr)}

        if isinstance(expr, InListExpr):
            if isinstance(expr.operand, ColumnRef):
                values = [self._expr_to_mongo(o) for o in expr.options]
                op = "$nin" if expr.negated else "$in"
                return {expr.operand.name: {op: values}}
            cond = {"$in": [self._expr_to_mongo(expr.operand), [self._expr_to_mongo(o) for o in expr.options]]}
            return {"$expr": {"$not": cond}} if expr.negated else {"$expr": cond}

        if isinstance(expr, BetweenExpr):
            if isinstance(expr.operand, ColumnRef):
                lo = self._expr_to_mongo(expr.low)
                hi = self._expr_to_mongo(expr.high)
                query = {expr.operand.name: {"$gte": lo, "$lte": hi}}
                return {"$nor": [query]} if expr.negated else query
            cond = {
                "$and": [
                    {"$gte": [self._expr_to_mongo(expr.operand), self._expr_to_mongo(expr.low)]},
                    {"$lte": [self._expr_to_mongo(expr.operand), self._expr_to_mongo(expr.high)]},
                ]
            }
            return {"$expr": {"$not": cond} if expr.negated else {"$expr": cond}}

        if isinstance(expr, IsNullExpr):
            if isinstance(expr.operand, ColumnRef):
                return {expr.operand.name: {"$ne": None} if expr.negated else None}
            return {"$expr": self._expr_to_mongo(expr)}

        if isinstance(expr, LikeExpr):
            pattern = self._literal_string(expr.pattern)
            if pattern is not None and isinstance(expr.operand, ColumnRef):
                regex = "^" + pattern.replace("%", ".*").replace("_", ".") + "$"
                query = {expr.operand.name: {"$regex": regex}}
                return {"$nor": [query]} if expr.negated else query
            return {"$expr": self._expr_to_mongo(expr)}

        # Boolean column used directly, e.g. WHERE active.
        if isinstance(expr, ColumnRef):
            return {expr.name: True}

        # Fall back to an $expr-wrapped aggregation expression.
        return {"$expr": self._expr_to_mongo(expr)}

    def _handle_project(self, node: Project) -> None:
        project: Dict[str, Any] = {}
        has_star = False
        for name, expr in node.items:
            if name == "*" or (isinstance(expr, LiteralValue) and expr.value == "*"):
                has_star = True
                continue
            project[name] = self._expr_to_mongo(expr)
        if project:
            self._saw_project = True
            self._pipeline.append({"$project": project})
        elif has_star:
            # SELECT *: no projection needed; ensure all fields flow through.
            self._scan_projection = None

    def _handle_sort(self, node: Sort) -> None:
        sort_spec = {}
        temp_fields: List[str] = []
        for index, (expr, desc) in enumerate(node.keys):
            if isinstance(expr, ColumnRef):
                key = expr.name
            else:
                # MongoDB $sort accepts field paths, not expressions. Materialize
                # computed keys in a temporary field, then remove them so the
                # output shape is unchanged.
                key = f"_optisql_sort_{index}"
                self._pipeline.append(
                    {"$addFields": {key: self._expr_to_mongo(expr)}}
                )
                temp_fields.append(key)
            sort_spec[key] = -1 if desc else 1
        if sort_spec:
            self._pipeline.append({"$sort": sort_spec})
        if temp_fields:
            self._pipeline.append(
                {"$project": {field: 0 for field in temp_fields}}
            )

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
        # MongoDB has no standalone DISTINCT in aggregation; the correct
        # lowering groups by the output fields and projects them back out.
        # We can only do that when the fields are statically enumerable.
        fields = self._distinct_fields(node)
        if fields is None:
            self.diagnostics.error(
                "E7004",
                "MongoDB DISTINCT requires an enumerable projection; "
                "SELECT DISTINCT * over an unknown field set is not supported",
                None,
            )
            return
        self._had_distinct = True
        group_id: Dict[str, Any] = {f: "$" + f for f in fields}
        self._pipeline.append({"$group": {"_id": group_id}})
        projection: Dict[str, Any] = {"_id": 0}
        for f in fields:
            projection[f] = "$_id." + f
        self._pipeline.append({"$project": projection})

    @staticmethod
    def _distinct_fields(node: Distinct) -> Optional[List[str]]:
        """Field names produced by the input to a DISTINCT, if enumerable."""
        source = node.input
        if isinstance(source, Project):
            fields: List[str] = []
            for name, expr in source.items:
                if name == "*" or (isinstance(expr, LiteralValue) and expr.value == "*"):
                    return None
                fields.append(name)
            return fields or None
        if isinstance(source, Scan) and source.projection:
            return list(source.projection)
        return None

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
        if isinstance(expr, FunctionExpr):
            return self._scalar_function_to_mongo(expr)
        if isinstance(expr, CaseExpr):
            return self._case_to_mongo(expr)
        self._failed = True
        self.diagnostics.error(
            "E7005",
            f"MongoDB codegen cannot translate expression '{type(expr).__name__}'",
            None,
        )
        return None

    # -- Scalar functions and CASE ----------------------------------------

    def _scalar_function_to_mongo(self, func: FunctionExpr) -> Any:
        """Translate a supported scalar function to a MongoDB expression."""
        name = func.name.upper()
        args = [self._expr_to_mongo(a) for a in func.args]
        single = args[0] if args else None

        if name == "ABS" and len(args) == 1:
            return {"$abs": single}
        if name == "LOWER" and len(args) == 1:
            return {"$toLower": single}
        if name == "UPPER" and len(args) == 1:
            return {"$toUpper": single}
        if name == "LENGTH" and len(args) == 1:
            return {"$strLenCP": single}
        if name == "ROUND" and len(args) in (1, 2):
            place = args[1] if len(args) == 2 else 0
            return {"$round": [single, place]}
        if name == "COALESCE" and args:
            expr = args[-1]
            for arg in reversed(args[:-1]):
                expr = {"$ifNull": [arg, expr]}
            return expr

        self._failed = True
        self.diagnostics.error(
            "E7005",
            f"MongoDB codegen does not support scalar function {name}",
            None,
        )
        return None

    def _case_to_mongo(self, expr: CaseExpr) -> Any:
        """Translate a searched CASE expression using nested $cond."""
        result = self._expr_to_mongo(expr.default) if expr.default is not None else None
        for when in reversed(expr.whens):
            condition = self._expr_to_mongo(when.condition)
            value = self._expr_to_mongo(when.result)
            result = {"$cond": [condition, value, result]}
        return result


    @staticmethod
    def _literal_string(expr: IRExpr) -> Optional[str]:
        if isinstance(expr, LiteralValue) and isinstance(expr.value, str):
            return expr.value
        return None


def generate_mongodb(plan: LogicalPlan, diagnostics: Optional[DiagnosticBag] = None) -> MongoPipelineResult:
    """Convenience function to generate a MongoDB aggregation pipeline."""
    return MongoDBGenerator(diagnostics).generate(plan)
