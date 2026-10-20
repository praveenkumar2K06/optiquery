"""Compiler service orchestration and mock execution engine for OptiQuery Web."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from ..backends import available_backends, get_backend
from ..backends.base import BackendCapability
from ..compiler import Compiler
from ..diagnostics import Diagnostic, Severity
from ..ir import LogicalPlan, plan_to_json, plan_to_string
from ..lexer.lexer import Lexer
from ..metadata import Catalog, CollectionSchema, FieldDef, FieldType
from ..optimizer import QueryOptimizer
from ..optimizer.cost import CostModel
from ..planner import lower
from .ast_serializer import ast_to_dict
from .models import (
    BackendInfo,
    CandidatePlanModel,
    CompileRequest,
    CompileResponse,
    CostBreakdown,
    CostComparison,
    CostComponent,
    DiagnosticModel,
    ExecuteRequest,
    ExecuteResponse,
    OptimizationTraceEntry,
    PresetQuery,
    SchemaItem,
    TokenModel,
)

# ---------------------------------------------------------------------------
# Preloaded Schemas & Sample Datasets
# ---------------------------------------------------------------------------

ECOMMERCE_SCHEMA = {
    "collections": {
        "users": {
            "cardinality": 10000,
            "avg_doc_size": 300,
            "fields": {
                "id": {"type": "integer", "indexed": True, "nullable": False},
                "name": {"type": "string"},
                "age": {"type": "integer", "indexed": True},
                "country": {"type": "string", "indexed": True},
                "email": {"type": "string", "nullable": True},
            },
        },
        "orders": {
            "cardinality": 50000,
            "avg_doc_size": 200,
            "fields": {
                "id": {"type": "integer", "indexed": True},
                "user_id": {"type": "integer", "indexed": True},
                "total": {"type": "decimal"},
                "status": {"type": "string"},
            },
        },
    }
}

ECOMMERCE_DATA = {
    "users": [
        {"id": 1, "name": "Alice Smith", "age": 28, "country": "USA", "email": "alice@example.com"},
        {"id": 2, "name": "Bob Jones", "age": 34, "country": "Canada", "email": "bob@example.com"},
        {"id": 3, "name": "Charlie Brown", "age": 19, "country": "USA", "email": "charlie@example.com"},
        {"id": 4, "name": "Diana Prince", "age": 42, "country": "UK", "email": "diana@example.com"},
        {"id": 5, "name": "Evan Wright", "age": 23, "country": "USA", "email": "evan@example.com"},
        {"id": 6, "name": "Fiona Gallagher", "age": 31, "country": "Canada", "email": "fiona@example.com"},
        {"id": 7, "name": "George Clark", "age": 55, "country": "Germany", "email": "george@example.com"},
        {"id": 8, "name": "Hannah Abbott", "age": 17, "country": "UK", "email": "hannah@example.com"},
        {"id": 9, "name": "Ian Malcolm", "age": 40, "country": "USA", "email": "ian@example.com"},
        {"id": 10, "name": "Julia Roberts", "age": 29, "country": "Germany", "email": "julia@example.com"},
    ],
    "orders": [
        {"id": 101, "user_id": 1, "total": 120.50, "status": "completed"},
        {"id": 102, "user_id": 1, "total": 45.00, "status": "completed"},
        {"id": 103, "user_id": 2, "total": 310.00, "status": "shipped"},
        {"id": 104, "user_id": 3, "total": 15.75, "status": "pending"},
        {"id": 105, "user_id": 4, "total": 520.00, "status": "completed"},
        {"id": 106, "user_id": 5, "total": 85.20, "status": "cancelled"},
        {"id": 107, "user_id": 6, "total": 210.00, "status": "completed"},
        {"id": 108, "user_id": 7, "total": 99.99, "status": "completed"},
        {"id": 109, "user_id": 9, "total": 430.50, "status": "shipped"},
        {"id": 110, "user_id": 10, "total": 60.00, "status": "pending"},
    ],
}

ANALYTICS_SCHEMA = {
    "collections": {
        "pageviews": {
            "cardinality": 100000,
            "avg_doc_size": 150,
            "fields": {
                "id": {"type": "integer", "indexed": True},
                "path": {"type": "string", "indexed": True},
                "visitor_id": {"type": "integer", "indexed": True},
                "duration_sec": {"type": "integer"},
                "device": {"type": "string"},
            },
        }
    }
}

ANALYTICS_DATA = {
    "pageviews": [
        {"id": 1, "path": "/home", "visitor_id": 501, "duration_sec": 45, "device": "mobile"},
        {"id": 2, "path": "/products", "visitor_id": 502, "duration_sec": 120, "device": "desktop"},
        {"id": 3, "path": "/pricing", "visitor_id": 501, "duration_sec": 30, "device": "mobile"},
        {"id": 4, "path": "/docs", "visitor_id": 503, "duration_sec": 300, "device": "desktop"},
        {"id": 5, "path": "/home", "visitor_id": 504, "duration_sec": 10, "device": "tablet"},
        {"id": 6, "path": "/products", "visitor_id": 505, "duration_sec": 85, "device": "desktop"},
        {"id": 7, "path": "/checkout", "visitor_id": 502, "duration_sec": 95, "device": "desktop"},
        {"id": 8, "path": "/pricing", "visitor_id": 506, "duration_sec": 150, "device": "mobile"},
    ]
}

SCHEMAS: Dict[str, SchemaItem] = {
    "ecommerce": SchemaItem(
        id="ecommerce",
        name="E-Commerce (Users & Orders)",
        description="Standard schema with users and orders collections, indexing on id, age, and country.",
        schema_dict=ECOMMERCE_SCHEMA,
    ),
    "analytics": SchemaItem(
        id="analytics",
        name="Web Analytics (Pageviews)",
        description="Telemetry and web traffic collection with path, duration, and device fields.",
        schema_dict=ANALYTICS_SCHEMA,
    ),
}

PRESETS: List[PresetQuery] = [
    PresetQuery(
        id="basic_filter_project",
        title="Filter, Sort & Limit",
        category="Basic Queries",
        description="Select specific columns with inequality filtering, descending sort, and row limits.",
        sql="SELECT name, age FROM users WHERE age >= 18 ORDER BY age DESC LIMIT 5",
        backend="mongodb",
        schema_name="ecommerce",
    ),
    PresetQuery(
        id="aggregation_grouping",
        title="Group By & Aggregates with Having",
        category="Aggregations",
        description="Groups documents by country and computes counts and average age, filtered by HAVING.",
        sql="SELECT country, COUNT(*) AS user_count, AVG(age) AS avg_age FROM users GROUP BY country HAVING COUNT(*) >= 2",
        backend="mongodb",
        schema_name="ecommerce",
    ),
    PresetQuery(
        id="opt_projection_pushdown",
        title="Optimizer: Projection Pushdown",
        category="Optimizer Passes",
        description="Reduces document fields early in the pipeline to cut down data movement and memory overhead.",
        sql="SELECT u.name, u.country FROM users u WHERE u.age > 21",
        backend="mongodb",
        schema_name="ecommerce",
    ),
    PresetQuery(
        id="opt_constant_folding",
        title="Optimizer: Constant Folding",
        category="Optimizer Passes",
        description="Folds constant arithmetic and boolean tautologies (e.g. 10 + 8 -> 18, 1 = 1) at compile time.",
        sql="SELECT name FROM users WHERE 1 = 1 AND age >= 10 + 8",
        backend="mongodb",
        schema_name="ecommerce",
    ),
    PresetQuery(
        id="opt_filter_reordering",
        title="Optimizer: Filter Reordering",
        category="Optimizer Passes",
        description="Heuristic filter reordering prioritizes indexed equality predicates before unindexed range predicates.",
        sql="SELECT name FROM users WHERE age > 20 AND id = 4 AND country = 'USA'",
        backend="mongodb",
        schema_name="ecommerce",
    ),
    PresetQuery(
        id="redis_key_lookup",
        title="Redis: Primary Key Lookup",
        category="Redis Backend",
        description="Translates single primary key equality lookup into efficient Redis HGETALL command.",
        sql="SELECT * FROM users WHERE id = 1",
        backend="redis",
        schema_name="ecommerce",
    ),
    PresetQuery(
        id="analytics_aggregation",
        title="Analytics: Device Aggregation",
        category="Aggregations",
        description="Analyzes average page duration across visitor device categories.",
        sql="SELECT device, COUNT(*) AS views, AVG(duration_sec) AS avg_duration FROM pageviews GROUP BY device ORDER BY views DESC",
        backend="mongodb",
        schema_name="analytics",
    ),
    PresetQuery(
        id="syntax_error_demo",
        title="Diagnostics: Syntax & Semantic Errors",
        category="Diagnostics",
        description="Demonstrates rich compiler diagnostics with source spans, line numbers, and error codes.",
        sql="SELECT name, non_existent_column FROM users WHERE age > 'twenty'",
        backend="mongodb",
        schema_name="ecommerce",
    ),
]


class CompilerService:
    """Core service for compiling and simulating SQL-to-NoSQL queries."""

    def build_catalog(
        self,
        schema_name: Optional[str] = "ecommerce",
        custom_schema: Optional[Dict[str, Any]] = None,
        strict: bool = False,
    ) -> Catalog:
        catalog = Catalog(strict=strict)
        raw_schema = custom_schema
        if raw_schema is None and schema_name in SCHEMAS:
            raw_schema = SCHEMAS[schema_name].schema_dict

        if raw_schema and "collections" in raw_schema:
            for coll_name, spec in raw_schema["collections"].items():
                schema = catalog.add_collection(CollectionSchema(name=coll_name))
                schema.cardinality = spec.get("cardinality")
                schema.avg_doc_size = spec.get("avg_doc_size")
                for field_name, field_spec in spec.get("fields", {}).items():
                    schema.add_field(
                        FieldDef(
                            name=field_name,
                            type=FieldType(field_spec.get("type", "any")),
                            indexed=bool(field_spec.get("indexed", False)),
                            nullable=bool(field_spec.get("nullable", True)),
                        )
                    )
        return catalog

    def extract_tokens(self, sql: str) -> List[TokenModel]:
        lexer = Lexer(sql)
        tokens = lexer.tokenize()
        result: List[TokenModel] = []
        for t in tokens:
            if t.type.name == "EOF":
                continue
            span = t.span
            length = span.end.offset - span.start.offset if span else len(t.lexeme)
            result.append(
                TokenModel(
                    type=t.type.name,
                    value=t.value,
                    lexeme=t.lexeme,
                    line=span.start.line if span else 1,
                    column=span.start.column if span else 1,
                    length=length,
                )
            )
        return result

    def compile(self, req: CompileRequest) -> CompileResponse:
        catalog = self.build_catalog(
            schema_name=req.schema_name,
            custom_schema=req.custom_schema,
            strict=req.strict_catalog,
        )

        tokens = self.extract_tokens(req.sql)

        compiler = Compiler(
            catalog=catalog,
            backend=req.backend,
            enable_optimizations=req.enable_optimizations,
        )

        result = compiler.compile(req.sql)

        # Convert diagnostics
        diag_models: List[DiagnosticModel] = []
        for d in result.all_diagnostics():
            span = d.span
            length = (span.end.offset - span.start.offset) if span else None
            diag_models.append(
                DiagnosticModel(
                    code=d.code,
                    severity=d.severity.value,
                    message=d.message,
                    line=span.start.line if span else None,
                    column=span.start.column if span else None,
                    offset=span.start.offset if span else None,
                    length=length,
                )
            )

        # Initial logical plan
        initial_plan_json = None
        initial_plan_text = None
        if result.logical_plan is not None:
            initial_plan_json = plan_to_json(result.logical_plan)
            initial_plan_text = plan_to_string(result.logical_plan)

        # Optimized plan
        optimized_plan_json = None
        optimized_plan_text = None
        if result.optimized_plan is not None:
            optimized_plan_json = plan_to_json(result.optimized_plan)
            optimized_plan_text = plan_to_string(result.optimized_plan)

        # AST representation
        ast_dict = ast_to_dict(result.statement) if result.statement else None
        ast_repr = repr(result.statement) if result.statement else None

        # Optimizer details & Cost comparison
        trace_entries: List[OptimizationTraceEntry] = []
        candidate_models: List[CandidatePlanModel] = []
        cost_breakdown_model: Optional[CostBreakdown] = None
        cost_comp: Optional[CostComparison] = None
        selection_rationale = None

        cost_model = CostModel(catalog=catalog)

        if result.logical_plan is not None:
            original_cost_obj = cost_model.estimate(result.logical_plan)
            original_total = original_cost_obj.total

            if result.optimization is not None:
                selection_rationale = result.optimization.selection_rationale
                for t in result.optimization.trace:
                    trace_entries.append(
                        OptimizationTraceEntry(
                            pass_name=t.get("pass", ""),
                            changes=t.get("changes", 0),
                            details=t.get("details", {}),
                        )
                    )

                for cand in result.optimization.candidates:
                    candidate_models.append(
                        CandidatePlanModel(
                            rule=cand.rule,
                            cost_total=cand.cost.total,
                            rationale=cand.rationale,
                        )
                    )

                opt_cost = result.optimization.cost_breakdown
                if opt_cost is not None:
                    comps = {
                        k: CostComponent(name=v.name, value=v.value, notes=v.notes)
                        for k, v in opt_cost.components.items()
                    }
                    cost_breakdown_model = CostBreakdown(
                        total=opt_cost.total,
                        components=comps,
                        approximate=opt_cost.approximate,
                        notes=opt_cost.notes,
                    )
                    optimized_total = opt_cost.total
                else:
                    optimized_total = original_total
            else:
                optimized_total = original_total

            savings_pct = (
                ((original_total - optimized_total) / original_total * 100.0)
                if original_total > 0
                else 0.0
            )
            cost_comp = CostComparison(
                original_cost=round(original_total, 6),
                optimized_cost=round(optimized_total, 6),
                savings_pct=round(savings_pct, 2),
                is_lower=optimized_total < original_total,
            )

        # Backend operation
        operation = result.backend_operation
        generated_query = operation.query if operation is not None else None
        query_formatted = None
        collection = operation.collection if operation is not None else ""
        notes = operation.notes if operation is not None else []

        if generated_query is not None:
            query_formatted = json.dumps(generated_query, indent=2, default=str)

        return CompileResponse(
            ok=result.ok,
            sql=req.sql,
            backend=result.backend,
            ast=ast_dict,
            ast_repr=ast_repr,
            tokens=tokens,
            initial_plan=initial_plan_json,
            initial_plan_text=initial_plan_text,
            optimized_plan=optimized_plan_json,
            optimized_plan_text=optimized_plan_text,
            optimization_trace=trace_entries,
            candidates=candidate_models,
            cost_breakdown=cost_breakdown_model,
            cost_comparison=cost_comp,
            generated_query=generated_query,
            query_formatted=query_formatted,
            collection=collection,
            notes=notes,
            diagnostics=diag_models,
            selection_rationale=selection_rationale,
        )

    def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        """Execute or simulate execution of the query against sample data."""
        start = time.perf_counter()
        comp_res = self.compile(
            CompileRequest(
                sql=req.sql,
                backend=req.backend,
                schema_name=req.schema_name,
                custom_schema=req.custom_schema,
                enable_optimizations=req.enable_optimizations,
            )
        )

        if not comp_res.ok:
            elapsed = (time.perf_counter() - start) * 1000.0
            errors = [d.message for d in comp_res.diagnostics if d.severity == "error"]
            return ExecuteResponse(
                success=False,
                elapsed_ms=round(elapsed, 3),
                error="; ".join(errors) or "Compilation failed",
                backend=req.backend,
                is_simulation=True,
            )

        # Determine target collection and dataset
        target_coll = comp_res.collection or "users"
        data_source = ECOMMERCE_DATA if req.schema_name != "analytics" else ANALYTICS_DATA
        records = data_source.get(target_coll, [])

        # In-memory execution simulator for MongoDB Aggregation Pipeline or Redis command
        try:
            simulated_rows = self._simulate_pipeline(comp_res.backend, comp_res.generated_query, records)
            elapsed = (time.perf_counter() - start) * 1000.0
            columns = list(simulated_rows[0].keys()) if simulated_rows else []
            return ExecuteResponse(
                success=True,
                rows=simulated_rows,
                row_count=len(simulated_rows),
                elapsed_ms=round(elapsed, 3),
                backend=comp_res.backend,
                is_simulation=True,
                columns=columns,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ExecuteResponse(
                success=False,
                elapsed_ms=round(elapsed, 3),
                error=f"Simulation error: {exc}",
                backend=comp_res.backend,
                is_simulation=True,
            )

    def _simulate_pipeline(
        self, backend: str, query: Any, dataset: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """A lightweight simulator for MongoDB aggregation pipelines and Redis commands."""
        import copy

        current = [copy.deepcopy(doc) for doc in dataset]

        if backend == "redis":
            if isinstance(query, list):
                for cmd in query:
                    if isinstance(cmd, dict) and cmd.get("command") == "HGETALL":
                        args = cmd.get("args", [])
                        if args:
                            key_parts = str(args[0]).split(":")
                            pk_val = key_parts[-1]
                            matched = [
                                doc for doc in current
                                if str(doc.get("id")) == str(pk_val)
                            ]
                            return matched
            return current[:5]

        # MongoDB Aggregation Pipeline
        if not isinstance(query, list):
            return current

        for stage in query:
            if not isinstance(stage, dict):
                continue
            stage_name = next(iter(stage))
            stage_val = stage[stage_name]

            if stage_name == "$match":
                current = [doc for doc in current if self._matches_filter(doc, stage_val)]
            elif stage_name == "$sort":
                for field_name, order in reversed(list(stage_val.items())):
                    reverse = order == -1
                    current.sort(key=lambda d: (d.get(field_name) is None, d.get(field_name)), reverse=reverse)
            elif stage_name == "$skip":
                skip_n = int(stage_val)
                current = current[skip_n:]
            elif stage_name == "$limit":
                lim_n = int(stage_val)
                current = current[:lim_n]
            elif stage_name == "$group":
                current = self._simulate_group(current, stage_val)
            elif stage_name == "$project":
                current = self._simulate_project(current, stage_val)

        return current

    def _matches_filter(self, doc: Dict[str, Any], filter_doc: Dict[str, Any]) -> bool:
        """Evaluate a Mongo-style match document against an in-memory document."""
        for key, val in filter_doc.items():
            if key == "$and":
                if not all(self._matches_filter(doc, sub) for sub in val):
                    return False
                continue
            if key == "$or":
                if not any(self._matches_filter(doc, sub) for sub in val):
                    return False
                continue

            doc_val = doc.get(key)
            if isinstance(val, dict):
                for op, expected in val.items():
                    if op == "$eq" and doc_val != expected:
                        return False
                    if op == "$ne" and doc_val == expected:
                        return False
                    if op == "$gt" and (doc_val is None or doc_val <= expected):
                        return False
                    if op == "$gte" and (doc_val is None or doc_val < expected):
                        return False
                    if op == "$lt" and (doc_val is None or doc_val >= expected):
                        return False
                    if op == "$lte" and (doc_val is None or doc_val > expected):
                        return False
                    if op == "$in" and doc_val not in expected:
                        return False
                    if op == "$nin" and doc_val in expected:
                        return False
            else:
                if doc_val != val:
                    return False
        return True

    def _simulate_project(self, docs: List[Dict[str, Any]], proj_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for doc in docs:
            new_doc = {}
            for target_field, src in proj_spec.items():
                if isinstance(src, str) and src.startswith("$"):
                    src_col = src[1:]
                    new_doc[target_field] = doc.get(src_col)
                elif isinstance(src, int) and src == 1:
                    new_doc[target_field] = doc.get(target_field)
                else:
                    new_doc[target_field] = doc.get(target_field)
            out.append(new_doc)
        return out

    def _simulate_group(self, docs: List[Dict[str, Any]], group_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        id_expr = group_spec.get("_id")
        grouped: Dict[Any, List[Dict[str, Any]]] = {}

        for doc in docs:
            key = None
            if isinstance(id_expr, str) and id_expr.startswith("$"):
                key = doc.get(id_expr[1:])
            elif isinstance(id_expr, dict):
                key = tuple(doc.get(v[1:] if isinstance(v, str) and v.startswith("$") else v) for v in id_expr.values())
            else:
                key = str(id_expr)
            grouped.setdefault(key, []).append(doc)

        result = []
        for group_key, items in grouped.items():
            row: Dict[str, Any] = {}
            if isinstance(id_expr, str) and id_expr.startswith("$"):
                row[id_expr[1:]] = group_key
            else:
                row["_id"] = group_key

            for agg_name, agg_spec in group_spec.items():
                if agg_name == "_id":
                    continue
                if isinstance(agg_spec, dict):
                    op = next(iter(agg_spec))
                    arg = agg_spec[op]
                    if op == "$sum":
                        if arg == 1:
                            row[agg_name] = len(items)
                        elif isinstance(arg, str) and arg.startswith("$"):
                            vals = [i.get(arg[1:], 0) for i in items if i.get(arg[1:]) is not None]
                            row[agg_name] = sum(vals)
                    elif op == "$avg":
                        if isinstance(arg, str) and arg.startswith("$"):
                            vals = [i.get(arg[1:], 0) for i in items if i.get(arg[1:]) is not None]
                            row[agg_name] = round(sum(vals) / len(vals), 2) if vals else 0
                    elif op == "$min":
                        vals = [i.get(arg[1:]) for i in items if i.get(arg[1:]) is not None]
                        row[agg_name] = min(vals) if vals else None
                    elif op == "$max":
                        vals = [i.get(arg[1:]) for i in items if i.get(arg[1:]) is not None]
                        row[agg_name] = max(vals) if vals else None

            result.append(row)
        return result

    def get_backends_info(self) -> List[BackendInfo]:
        res: List[BackendInfo] = []
        for name in available_backends():
            backend = get_backend(name)
            caps = [c.value for c in getattr(backend, "capabilities", set())]
            if name == "mongodb":
                res.append(
                    BackendInfo(
                        name="mongodb",
                        title="MongoDB Aggregation Pipeline",
                        description="Translates SQL AST into optimized MongoDB aggregation stages ($match, $project, $sort, $limit, $group).",
                        capabilities=caps,
                        output_format="JSON Pipeline Array",
                    )
                )
            elif name == "redis":
                res.append(
                    BackendInfo(
                        name="redis",
                        title="Redis Command Sequences",
                        description="Translates primary key lookups into hash and string commands (e.g. HGETALL, GET, ZRANGEBYSCORE).",
                        capabilities=caps,
                        output_format="Redis CLI Command List",
                    )
                )
        return res

