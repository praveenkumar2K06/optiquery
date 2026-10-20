"""MongoDB backend adapter (aggregation pipelines).

Generation always works and never needs a server or driver. Execution is
optional and only enabled when:

* ``pymongo`` is importable, **and**
* a client or connection URI is explicitly provided.

The adapter never hardcodes credentials. If no client/URI is supplied it can
optionally read the URI from the ``OPTISQL_MONGODB_URI`` environment variable
when :meth:`MongoDBBackend.from_environment` is used.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from ..codegen.mongodb import MongoPipelineResult, generate_mongodb
from ..diagnostics import DiagnosticBag
from ..ir import LogicalPlan
from .base import Backend, BackendCapability, BackendOperation


def _pymongo_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("pymongo") is not None
    except Exception:  # pragma: no cover - defensive
        return False


class MongoDBBackend(Backend):
    """MongoDB backend: generates aggregation pipelines and can run them."""

    name = "mongodb"
    description = "MongoDB aggregation pipeline backend"

    def __init__(
        self,
        database: Any = None,
        database_name: str = "optisql",
        uri: Optional[str] = None,
        client: Any = None,
    ) -> None:
        super().__init__()
        self._capabilities = {
            BackendCapability.SCAN,
            BackendCapability.FILTER,
            BackendCapability.PROJECT,
            BackendCapability.SORT,
            BackendCapability.LIMIT,
            BackendCapability.SKIP,
            BackendCapability.GROUP_BY,
            BackendCapability.AGGREGATES,
            BackendCapability.DISTINCT,
            BackendCapability.INDEX_AWARE,
        }
        if _pymongo_available():
            self._capabilities.add(BackendCapability.EXECUTION)

        self.database_name = database_name
        self.uri = uri
        self._client = client
        self._database = database
        if self._database is None and client is not None:
            self._database = client[database_name]
        elif self._database is None and uri is not None and _pymongo_available():
            self._database = self._connect(uri, database_name)

    # -- Construction ------------------------------------------------------

    @staticmethod
    def _connect(uri: str, database_name: str):
        import pymongo  # local import: only needed when connecting

        client = pymongo.MongoClient(uri)
        return client[database_name]

    @classmethod
    def from_environment(
        cls, variable: str = "OPTISQL_MONGODB_URI", database_name: str = "optisql"
    ) -> "MongoDBBackend":
        """Build a backend from a connection URI stored in the environment.

        Returns a generation-only backend when the variable is unset, so this
        is safe to call in environments without a database.
        """
        uri = os.environ.get(variable)
        return cls(uri=uri, database_name=database_name)

    def is_available(self) -> bool:
        return self._database is not None

    # -- Generation --------------------------------------------------------

    def generate(self, plan: LogicalPlan) -> BackendOperation:
        bag = DiagnosticBag()
        result: MongoPipelineResult = generate_mongodb(plan, bag)
        return BackendOperation(
            backend=self.name,
            supported=not bag.has_errors,
            query=result.pipeline,
            diagnostics=bag,
            collection=result.collection,
            notes=["MongoDB aggregation pipeline over collection "
                   f"'{result.collection}'"],
        )

    # -- Execution ---------------------------------------------------------

    def execute(self, plan: LogicalPlan, **kwargs: Any) -> List[Dict[str, Any]]:
        if not self.is_available():
            raise RuntimeError(
                "MongoDB backend has no configured connection; pass client=, "
                "uri=, or use from_environment() when a URI is configured."
            )
        operation = self.generate(plan)
        if not operation.supported:
            raise RuntimeError(
                "Plan cannot be represented as a MongoDB pipeline: "
                + "; ".join(d.message for d in operation.diagnostics.errors)
            )
        collection = self._database[operation.collection]
        cursor = collection.aggregate(operation.query, **kwargs)
        return list(cursor)

    def timed_execute(self, plan: LogicalPlan, **kwargs: Any):
        """Execute and return ``(rows, elapsed_ms)`` for benchmark reporting."""
        start = time.perf_counter()
        rows = self.execute(plan, **kwargs)
        return rows, (time.perf_counter() - start) * 1000.0


__all__ = ["MongoDBBackend"]
