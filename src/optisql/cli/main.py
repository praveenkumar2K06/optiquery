"""Command-line entry point for the OptiQuery compiler.

Examples::

    python -m optisql "SELECT name FROM users WHERE age >= 18 LIMIT 10"
    python -m optisql --backend redis "SELECT * FROM users WHERE id = '1'"
    python -m optisql --no-optimize --explain "SELECT * FROM users"
    python -m optisql --schema examples/schema.json "SELECT * FROM users"
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from ..compiler import Compiler
from ..ir import plan_to_json, plan_to_string
from ..metadata import Catalog, CollectionSchema, FieldDef, FieldType


def _load_catalog(path: Optional[str]) -> Catalog:
    if not path:
        # Permissive catalog when no schema is supplied so pure compilation
        # works without any database credentials or metadata.
        return Catalog(strict=False)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    catalog = Catalog(strict=False)
    for coll, spec in data.get("collections", {}).items():
        schema = catalog.add_collection(CollectionSchema(name=coll))
        schema.cardinality = spec.get("cardinality")
        schema.avg_doc_size = spec.get("avg_doc_size")
        for field_name, field_spec in spec.get("fields", {}).items():
            schema.add_field(
                FieldDef(
                    name=field_name,
                    type=FieldType(field_spec.get("type", "any")),
                    indexed=bool(field_spec.get("indexed", False)),
                )
            )
    return catalog


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="optisql", description="OptiQuery SQL-to-NoSQL compiler")
    ap.add_argument("sql", nargs="?", help="SQL query text to compile")
    ap.add_argument("--backend", "-b", default="mongodb", choices=["mongodb", "redis"])
    ap.add_argument("--schema", "-s", help="Path to a JSON schema file")
    ap.add_argument("--no-optimize", action="store_true", help="Disable optimization passes")
    ap.add_argument("--explain", action="store_true", help="Print plan explanations")
    ap.add_argument("--ast", action="store_true", help="Print the parsed AST")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.sql:
        build_parser().print_help()
        return 2

    catalog = _load_catalog(args.schema)
    compiler = Compiler(
        catalog=catalog,
        backend=args.backend,
        enable_optimizations=not args.no_optimize,
    )
    result = compiler.compile(args.sql)

    if args.json:
        payload = {
            "ok": result.ok,
            "backend": result.backend,
            "plan": plan_to_json(result.optimized_plan) if result.optimized_plan else None,
            "pipeline": getattr(result.generated, "pipeline", None),
            "commands": getattr(result.generated, "commands", None),
            "cost": (
                result.optimization.cost_breakdown.total
                if result.optimization and result.optimization.cost_breakdown
                else None
            ),
            "diagnostics": [
                {
                    "code": d.code,
                    "severity": d.severity.value,
                    "message": d.message,
                }
                for d in result.diagnostics.items
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.ok else 1

    if args.ast and result.statement is not None:
        print("=== AST ===")
        print(repr(result.statement))
        print()

    if result.logical_plan is not None:
        print("=== Logical Plan (original) ===")
        print(plan_to_string(result.logical_plan))
        print()

    if result.optimization is not None:
        print("=== Logical Plan (optimized) ===")
        print(plan_to_string(result.optimization.optimized))
        print()
        if args.explain:
            print("=== Optimization Trace ===")
            for entry in result.optimization.trace:
                print(f"  - {entry['pass']}: changes={entry['changes']}")
            cost = result.optimization.cost_breakdown
            if cost is not None:
                print()
                print("=== Estimated Cost (approximate) ===")
                for name, comp in cost.components.items():
                    print(f"  {name:<10} {comp.value:.6f}  {comp.notes}")
                print(f"  {'TOTAL':<10} {cost.total:.6f}")
            print()

    generated = result.generated
    if generated is not None:
        pipeline = getattr(generated, "pipeline", None)
        commands = getattr(generated, "commands", None)
        if pipeline is not None:
            print("=== MongoDB Aggregation Pipeline ===")
            print(json.dumps(pipeline, indent=2, default=str))
        if commands is not None:
            print("=== Redis Commands ===")
            print(json.dumps(commands, indent=2, default=str))

    if result.diagnostics.items:
        print()
        print("=== Diagnostics ===")
        for diag in result.diagnostics.items:
            print("  " + diag.format())

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
