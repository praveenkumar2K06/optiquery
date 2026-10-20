"""Redis command generation for a well-defined, documented data model.

Redis is a key-value store, not a relational engine. OptiQuery only generates
Redis commands for query shapes that can be implemented *correctly* under an
explicit data model. Anything else produces a structured error rather than a
query that silently returns wrong results.

Redis data model
----------------

* A logical collection ``users`` is stored as one Redis **hash per document**.
  The hash key is ``{key_prefix}:{collection}:{primary_key_value}``.
* The configured ``primary_key`` field (default ``id``) is unique per document.
* Field lookup on a non-primary field is only supported when the field is
  declared **indexed** in the data model. Indexed fields are backed by
  application-managed ``SET`` structures named
  ``{key_prefix}:idx:{collection}:{field}:{value}`` whose members are primary
  key values.
* Supported operations:

  - point lookup by primary key equality -> ``HGETALL``/``HMGET``,
  - equality on exactly one indexed field -> ``SMEMBERS`` on the index set,
  - projecting a subset of fields for a point lookup -> ``HMGET``.

* Everything else (range predicates, sorting, grouping, joins, aggregates,
  ``OR`` predicates, ``DISTINCT``) is unsupported and reported explicitly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..diagnostics import DiagnosticBag
from ..ir import (
    BinaryExpr,
    ColumnRef,
    Filter,
    IRExpr,
    LiteralValue,
    LogicalPlan,
    Project,
    Scan,
)


@dataclass
class RedisDataModel:
    """Configuration describing how logical collections map onto Redis."""

    key_prefix: str = "optisql"
    #: Field name treated as the unique document key.
    primary_key: str = "id"
    #: Fields that have application-managed index sets.
    indexed_fields: Dict[str, List[str]] = field(default_factory=dict)

    def hash_key(self, collection: str, pk_value: Any) -> str:
        return f"{self.key_prefix}:{collection}:{_stringify(pk_value)}"

    def index_key(self, collection: str, field_name: str, value: Any) -> str:
        return f"{self.key_prefix}:idx:{collection}:{field_name}:{_stringify(value)}"

    def is_indexed(self, collection: str, field_name: str) -> bool:
        return field_name in self.indexed_fields.get(collection, [])


@dataclass
class RedisResult:
    """Result of Redis command generation."""

    commands: List[Dict[str, Any]]
    diagnostics: DiagnosticBag
    collection: str
    supported: bool = True

    def to_json(self) -> str:
        return json.dumps(self.commands, indent=2, default=str)


class RedisGenerator:
    """Generates Redis commands for the supported query subset."""

    name = "redis"

    def __init__(
        self,
        data_model: Optional[RedisDataModel] = None,
        diagnostics: Optional[DiagnosticBag] = None,
    ):
        self.model = data_model or RedisDataModel()
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self.commands: List[Dict[str, Any]] = []
        self.collection = "unknown"
        self.supported = True

    # -- Public API --------------------------------------------------------

    def generate(self, plan: LogicalPlan) -> RedisResult:
        self.commands = []
        self.supported = True

        # Unwrap a projection over a filter/scan.
        projection: Optional[List[str]] = None
        node = plan
        if isinstance(node, Project):
            projection = []
            for name, expr in node.items:
                if name == "*" or (isinstance(expr, LiteralValue) and expr.value == "*"):
                    projection = None
                    break
                if isinstance(expr, ColumnRef):
                    projection.append(expr.name)
                else:
                    return self._unsupported(
                        "E8004",
                        "Redis backend only supports simple column projections",
                    )
            node = node.input

        if isinstance(node, Filter):
            scan = node.input
            predicate = node.predicate
        elif isinstance(node, Scan):
            scan = node
            predicate = None
        else:
            return self._unsupported(
                "E8001",
                f"Redis backend does not support operator {node.node_name()}",
            )

        if not isinstance(scan, Scan):
            return self._unsupported(
                "E8002",
                "Redis backend requires a single collection scan as the data source",
            )
        self.collection = scan.collection

        if predicate is None:
            # Full-collection scan is not representable without SCAN over a
            # known key namespace; refuse rather than guess.
            return self._unsupported(
                "E8005",
                "Redis backend requires an equality predicate on the primary key "
                "or an indexed field",
            )

        field_name, value = self._extract_equality(predicate)
        if field_name is None:
            return self._unsupported(
                "E8003",
                "Redis backend only supports a single equality predicate "
                "(field = literal)",
            )

        if field_name == self.model.primary_key:
            key = self.model.hash_key(self.collection, value)
            if projection:
                self.commands.append(
                    {
                        "command": "HMGET",
                        "args": [key, *projection],
                        "explanation": f"Fetch fields {projection} of {key}",
                    }
                )
            else:
                self.commands.append(
                    {
                        "command": "HGETALL",
                        "args": [key],
                        "explanation": f"Fetch full hash at {key}",
                    }
                )
        elif self.model.is_indexed(self.collection, field_name):
            index_key = self.model.index_key(self.collection, field_name, value)
            self.commands.append(
                {
                    "command": "SMEMBERS",
                    "args": [index_key],
                    "explanation": (
                        f"Look up primary keys via application-managed index "
                        f"{index_key}; fetch each with HGETALL/HMGET"
                    ),
                }
            )
        else:
            return self._unsupported(
                "E8006",
                f"Field '{field_name}' is not the primary key and is not declared "
                f"indexed for collection '{self.collection}'; Redis cannot filter "
                f"it without a supported index",
            )

        return RedisResult(
            commands=self.commands,
            diagnostics=self.diagnostics,
            collection=self.collection,
            supported=self.supported,
        )

    # -- Helpers -----------------------------------------------------------

    def _extract_equality(self, predicate: IRExpr):
        if not isinstance(predicate, BinaryExpr) or predicate.op != "=":
            return None, None
        left, right = predicate.left, predicate.right
        if isinstance(left, ColumnRef) and isinstance(right, LiteralValue):
            return left.name, right.value
        # Reversed operands: literal = field
        if isinstance(right, ColumnRef) and isinstance(left, LiteralValue):
            return right.name, left.value
        return None, None

    def _unsupported(self, code: str, message: str) -> RedisResult:
        self.supported = False
        self.diagnostics.error(code, message, None)
        return RedisResult(
            commands=[],
            diagnostics=self.diagnostics,
            collection=self.collection if self.collection != "unknown" else "",
            supported=False,
        )


def generate_redis(
    plan: LogicalPlan,
    data_model: Optional[RedisDataModel] = None,
    diagnostics: Optional[DiagnosticBag] = None,
) -> RedisResult:
    """Convenience wrapper for Redis command generation."""
    return RedisGenerator(data_model, diagnostics).generate(plan)


def _stringify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
