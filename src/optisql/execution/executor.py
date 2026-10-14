"""Optional execution of generated queries against real or mock backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

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


class QueryExecutor:
    """Drives execution. By default it is a dry-run that never touches a DB."""

    def __init__(self, backend: Optional[object] = None, enabled: bool = False):
        self.backend = backend
        self.enabled = enabled

    def execute(self, plan: LogicalPlan, **kwargs) -> ExecutionResult:
        if not self.enabled or self.backend is None:
            return ExecutionResult(
                success=True,
                executed=False,
                backend=getattr(self.backend, "name", "none"),
            )
        raise NotImplementedError(
            "Execution requires a configured backend adapter"
        )
