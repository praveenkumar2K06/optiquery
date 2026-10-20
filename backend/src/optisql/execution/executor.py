"""Optional execution of plans through a configured backend adapter.

Execution is **off by default**. It only happens when an executor is created
with ``enabled=True`` *and* a backend adapter that can actually reach a
database. Nothing here opens a network connection on its own; the backend
adapter owns connection configuration (client/URI). This keeps the compiler
usable in pure offline/compilation contexts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..backends.base import Backend
from ..ir import LogicalPlan


@dataclass
class ExecutionResult:
    """Result of executing a query, when execution is enabled."""

    success: bool
    rows: List[Any] = field(default_factory=list)
    elapsed_ms: Optional[float] = None
    error: Optional[str] = None
    backend: str = "none"
    executed: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)


class QueryExecutor:
    """Drives execution through a :class:`~optisql.backends.base.Backend`.

    By default it is a dry-run that never touches a database. When enabled and
    given a backend with a live connection, it delegates to
    ``backend.execute`` and reports timing.
    """

    def __init__(self, backend: Optional[Backend] = None, enabled: bool = False):
        self.backend = backend
        self.enabled = enabled

    def execute(self, plan: LogicalPlan, **kwargs: Any) -> ExecutionResult:
        backend_name = getattr(self.backend, "name", "none")

        if not self.enabled:
            return ExecutionResult(
                success=True, executed=False, backend=backend_name
            )

        if self.backend is None:
            return ExecutionResult(
                success=False,
                executed=False,
                backend="none",
                error="Execution was requested but no backend adapter was provided.",
            )

        if not self.backend.can_execute(plan):
            return ExecutionResult(
                success=False,
                executed=False,
                backend=backend_name,
                error=(
                    f"Backend '{backend_name}' cannot execute plans "
                    f"(missing driver or unsupported operation)."
                ),
            )

        if not self.backend.is_available():
            return ExecutionResult(
                success=False,
                executed=False,
                backend=backend_name,
                error=(
                    f"Backend '{backend_name}' has no configured connection."
                ),
            )

        start = time.perf_counter()
        try:
            rows = self.backend.execute(plan, **kwargs)
        except Exception as exc:  # pragma: no cover - depends on live DB
            return ExecutionResult(
                success=False,
                executed=True,
                backend=backend_name,
                error=str(exc),
                elapsed_ms=(time.perf_counter() - start) * 1000.0,
            )

        return ExecutionResult(
            success=True,
            executed=True,
            backend=backend_name,
            rows=list(rows),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )
