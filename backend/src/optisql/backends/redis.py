"""Redis backend adapter.

Redis is not a relational engine, so OptiQuery only generates commands for the
query shapes described by :class:`optisql.codegen.redis.RedisDataModel`
(point lookups by primary key, equality on an indexed field, and simple
projections). Everything else is rejected with a structured error by the
generator, so a plan is never translated into commands that would return wrong
results.

Execution is optional and requires the ``redis`` driver plus an explicit client
or URL.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from ..codegen.redis import RedisDataModel, RedisResult, generate_redis
from ..diagnostics import DiagnosticBag
from ..ir import LogicalPlan
from .base import Backend, BackendCapability, BackendOperation


def _redis_driver_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("redis") is not None
    except Exception:  # pragma: no cover - defensive
        return False


class RedisBackend(Backend):
    """Redis backend: generates commands for the documented subset."""

    name = "redis"
    description = "Redis key-value backend (point lookups and indexed equality)"

    def __init__(
        self,
        data_model: Optional[RedisDataModel] = None,
        client: Any = None,
        url: Optional[str] = None,
    ) -> None:
        super().__init__()
        # Capabilities reflect the operators the generator can express at all;
        # the generator still rejects unsupported *shapes* (e.g. ranges).
        self._capabilities = {
            BackendCapability.SCAN,
            BackendCapability.FILTER,
            BackendCapability.PROJECT,
            BackendCapability.INDEX_AWARE,
        }
        if _redis_driver_available():
            self._capabilities.add(BackendCapability.EXECUTION)

        self.model = data_model if data_model is not None else RedisDataModel()
        self._client = client
        if self._client is None and url is not None and _redis_driver_available():
            self._client = self._connect(url)

    # -- Construction ------------------------------------------------------

    @staticmethod
    def _connect(url: str):
        import redis  # local import: only needed when connecting

        return redis.Redis.from_url(url, decode_responses=True)

    @classmethod
    def from_environment(
        cls,
        variable: str = "OPTISQL_REDIS_URL",
        data_model: Optional[RedisDataModel] = None,
    ) -> "RedisBackend":
        """Build a backend from a URL stored in the environment."""
        url = os.environ.get(variable)
        return cls(data_model=data_model, url=url)

    def is_available(self) -> bool:
        return self._client is not None

    # -- Generation --------------------------------------------------------

    def generate(self, plan: LogicalPlan) -> BackendOperation:
        bag = DiagnosticBag()
        result: RedisResult = generate_redis(plan, self.model, bag)
        return BackendOperation(
            backend=self.name,
            supported=result.supported and not bag.has_errors,
            query=result.commands,
            diagnostics=bag,
            collection=result.collection,
            notes=["Redis commands assume the documented OptiQuery data model "
                   "(hash per document; indexed fields backed by SETs)"],
        )

    # -- Execution ---------------------------------------------------------

    def execute(self, plan: LogicalPlan, **kwargs: Any) -> List[Dict[str, Any]]:
        if not self.is_available():
            raise RuntimeError(
                "Redis backend has no configured client; pass client=, url=, "
                "or use from_environment() when a URL is configured."
            )
        operation = self.generate(plan)
        if not operation.supported:
            raise RuntimeError(
                "Plan cannot be represented as Redis commands: "
                + "; ".join(d.message for d in operation.diagnostics.errors)
            )
        return self._execute_commands(
            operation.query, operation.collection, **kwargs
        )

    def _execute_commands(
        self,
        commands: List[Dict[str, Any]],
        collection: str,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for command in commands:
            name = command["command"].upper()
            args = command["args"]
            if name == "HGETALL":
                record = self._client.hgetall(args[0])
                rows.append(dict(record) if record else {})
            elif name == "HMGET":
                key, *fields = args
                values = self._client.hmget(key, fields)
                rows.append({f: v for f, v in zip(fields, values) if v is not None})
            elif name == "SMEMBERS":
                members = self._client.smembers(args[0])
                for member in members:
                    hash_key = self.model.hash_key(collection, member)
                    record = self._client.hgetall(hash_key)
                    rows.append(dict(record) if record else {})
            else:  # pragma: no cover - generator only emits the above
                raise RuntimeError(f"Unsupported Redis command '{name}'")
        return rows

    def timed_execute(self, plan: LogicalPlan, **kwargs: Any):
        start = time.perf_counter()
        rows = self.execute(plan, **kwargs)
        return rows, (time.perf_counter() - start) * 1000.0


__all__ = ["RedisBackend"]
