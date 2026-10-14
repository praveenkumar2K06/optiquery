"""Common backend interface for NoSQL execution engines.

This interface keeps the compiler core independent of any specific backend by
defining capabilities, generation, and optional execution hooks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..ast import Statement
from ..diagnostics import DiagnosticBag
from ..ir import LogicalPlan
from ..optimizer import OptimizationResult
from ..symbols import SymbolTable


class BackendCapability(str, Enum):
    """Capabilities a backend may or may not support."""

    SCAN = "scan"
    FILTER = "filter"
    PROJECT = "project"
    SORT = "sort"
    LIMIT = "limit"
    GROUP_BY = "group_by"
    AGGREGATES = "aggregates"
    JOIN = "join"
    DISTINCT = "distinct"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    INDEX_AWARE = "index_aware"
    EXECUTION = "execution"


@dataclass
class CompilationResult:
    """Consistent compilation result across all backends."""

    success: bool
    backend: str
    source_sql: str
    statement: Optional[Statement] = None
    symbols: Optional[SymbolTable] = None
    original_plan: Optional[LogicalPlan] = None
    optimized_plan: Optional[LogicalPlan] = None
    optimization: Optional[OptimizationResult] = None
    generated_query: Any = None
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> List:
        return self.diagnostics.errors


class Backend:
    """Abstract base backend interface."""

    name = "base"

    def __init__(self):
        self.diagnostics = DiagnosticBag()
        self._capabilities: set = set()

    @property
    def capabilities(self) -> set:
        return set(self._capabilities)

    def supports(self, capability: BackendCapability) -> bool:
        return capability in self._capabilities

    def can_execute(self, plan: LogicalPlan) -> bool:
        return self.supports(BackendCapability.EXECUTION)

    def generate(self, plan: LogicalPlan) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def execute(self, plan: LogicalPlan, **kwargs) -> Any:  # pragma: no cover - interface
        raise NotImplementedError
