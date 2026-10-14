"""Redis command generation for supported operations.

Redis is not a general-purpose relational database. This backend defines a
specific, explicitly-documented data model and only generates commands for
queries that can be implemented correctly under that model. Unsupported
operations return structured diagnostics explaining why.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..diagnostics import DiagnosticBag
from ..ir import (
    BinaryExpr,
    ColumnRef,
    Filter,
    Limit,
    LiteralValue,
    LogicalPlan,
    Project,
    Scan,
)
from ..metadata import FieldType


@dataclass
class RedisResult:
    """Result of Redis command generation."""

    commands: List[Dict[str, Any]]
    diagnostics: DiagnosticBag
    collection: str

    def to_json(self) -> str:
        return json.dumps(self.commands, indent=2, default=str)


class RedisGenerator:
    """Generates Redis commands for a supported subset of query plans."""

    def __init__(self, diagnostics: Optional[DiagnosticBag] = None):
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self.commands: List[Dict[str, Any]] = []
        self.collection = "unknown"
        #: Configurable data model: hash per document with key prefix.
        self.key_prefix = "optisql:hash"

    def generate(self, plan: LogicalPlan) -> RedisResult:
        self.commands = []
        # Only handle a very restricted subset for now: simple equality lookups
        # on indexed-like fields that map to Redis hash keys.
        if not isinstance(plan, Filter) or not isinstance(plan.input, Scan):
            self.diagnostics.error(
                "E8001",
                "Redis backend only supports simple equality filters over a "
                "single collection scan in this subset",
                None,
            )
            return RedisResult(commands=[], diagnostics=self.diagnostics, collection="unknown")

        scan = plan.input
        self.collection = scan.collection
        pred = plan.predicate
        if not isinstance(pred, BinaryExpr) or pred.op != "=":
            self.diagnostics.error(
                "E8002",
                "Redis backend only supports equality predicates (field = value)",
                None,
            )
            return RedisResult(commands=[], diagnostics=self.diagnostics, collection="unknown")

        if not isinstance(pred.left, ColumnRef) or not isinstance(pred.right, LiteralValue):
            self.diagnostics.error(
                "E8003",
                "Redis backend requires predicate of form: column = literal",
                None,
            )
            return RedisResult(commands=[], diagnostics=self.diagnostics, collection="unknown")

        field = pred.left.name
        value = pred.right.value
        # Normalize value to string for key construction in this simple model.
        if isinstance(value, str):
            vstr = value
        else:
            vstr = str(value) if value is not None else "null"
        key = f"{self.key_prefix}:{self.collection}:{field}:{vstr}".replace("::", ":")  # normalize
        # but better: handle empty properly
        key = f"{self.key_prefix}:{self.collection}:{field}:{vstr}"  # keep as is, fix properly
        parts = [self.key_prefix, self.collection, field, vstr]
        # collapse empty segments while keeping structure readable
        key = ":".join(p for p in parts if p != "" or len(parts) == 4)  # simple approach
        if not field:
            key = f"{self.key_prefix}:{self.collection}:{vstr}"
        else:
            key = f"{self.key_prefix}:{self.collection}:{field}:{vstr}"

        cmd = {
            "command": "HGETALL",
            "args": [key],
            "explanation": f"Fetch document hash at {key}",
        }
        self.commands.append(cmd)

        # If the plan projects specific fields, we could emit HMGET instead.
        # For the basic subset, return the full hash.
        if isinstance(plan.input, Scan) and plan.input.projection:
            self.diagnostics.warning(
                "W8001",
                "Redis backend projection is not fully enforced in this subset",
                None,
            )

        # Handle limit if present.
        # A full implementation would scan/paginate; for now, document limit.
        parent = plan
        if isinstance(parent, Filter):
            # Walk further to see Limit above (simplified).
            pass

        return RedisResult(
            commands=self.commands, diagnostics=self.diagnostics, collection=self.collection
        )


def generate_redis(plan: LogicalPlan, diagnostics: Optional[DiagnosticBag] = None) -> RedisResult:
    """Convenience wrapper for Redis command generation."""
    return RedisGenerator(diagnostics).generate(plan)
