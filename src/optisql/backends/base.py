"""Common backend interface for NoSQL execution engines.

This interface keeps the compiler core independent of any specific backend by
defining capabilities, generation, validation, and optional execution hooks.

A backend adapter has three responsibilities:

* declare which :class:`BackendCapability` values it supports,
* :meth:`Backend.generate` a backend-specific query from an optimized logical
  plan, and
* optionally :meth:`Backend.execute` that query against a live database, but
  only when a driver is installed and a connection is explicitly configured.

Execution is never implicit. The default :class:`Backend` refuses to execute,
and concrete backends only claim :data:`BackendCapability.EXECUTION` when a
usable driver is available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from ..diagnostics import DiagnosticBag
from ..ir import (
    Delete,
    Distinct,
    Filter,
    Group,
    Insert,
    Join,
    Limit,
    LogicalPlan,
    Project,
    Scan,
    Skip,
    Sort,
    Update,
)


class BackendCapability(str, Enum):
    """Capabilities a backend may or may not support."""

    SCAN = "scan"
    FILTER = "filter"
    PROJECT = "project"
    SORT = "sort"
    LIMIT = "limit"
    SKIP = "skip"
    GROUP_BY = "group_by"
    AGGREGATES = "aggregates"
    JOIN = "join"
    DISTINCT = "distinct"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    INDEX_AWARE = "index_aware"
    EXECUTION = "execution"


#: Maps an IR plan operator to the capability required to run it.
NODE_CAPABILITY: Dict[type, BackendCapability] = {
    Scan: BackendCapability.SCAN,
    Filter: BackendCapability.FILTER,
    Project: BackendCapability.PROJECT,
    Sort: BackendCapability.SORT,
    Limit: BackendCapability.LIMIT,
    Skip: BackendCapability.SKIP,
    Group: BackendCapability.GROUP_BY,
    Distinct: BackendCapability.DISTINCT,
    Insert: BackendCapability.INSERT,
    Update: BackendCapability.UPDATE,
    Delete: BackendCapability.DELETE,
    Join: BackendCapability.JOIN,
}


@dataclass
class BackendOperation:
    """Normalized result of backend-specific query generation.

    Concrete generators (MongoDB pipelines, Redis commands) are wrapped in this
    shape so callers can treat all backends uniformly.
    """

    backend: str
    supported: bool
    query: Any
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    collection: str = ""
    #: Human-readable notes about how the query will be executed.
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.supported and not self.diagnostics.has_errors

    def to_json(self) -> str:
        import json

        return json.dumps(self.query, indent=2, default=str)


class Backend:
    """Abstract base backend interface."""

    name = "base"
    #: Human-readable description used in ``describe``.
    description = "abstract backend"

    def __init__(self) -> None:
        self.diagnostics = DiagnosticBag()
        self._capabilities: Set[BackendCapability] = set()

    # -- Capabilities ------------------------------------------------------

    @property
    def capabilities(self) -> Set[BackendCapability]:
        return set(self._capabilities)

    def supports(self, capability: BackendCapability) -> bool:
        return capability in self._capabilities

    def required_capabilities(self, plan: LogicalPlan) -> Set[BackendCapability]:
        """Collect the capabilities needed to run ``plan``.

        Traversal is iterative so arbitrary plan depth is safe.
        """
        required: Set[BackendCapability] = set()
        stack: List[LogicalPlan] = [plan]
        while stack:
            node = stack.pop()
            capability = NODE_CAPABILITY.get(type(node))
            if capability is not None:
                required.add(capability)
            if isinstance(node, Group) and node.aggregates:
                required.add(BackendCapability.AGGREGATES)
            stack.extend(node.inputs())
        return required

    def validate(self, plan: LogicalPlan) -> DiagnosticBag:
        """Return diagnostics for capabilities ``plan`` needs but unsupported."""
        bag = DiagnosticBag()
        for capability in sorted(self.required_capabilities(plan), key=lambda c: c.value):
            if not self.supports(capability):
                bag.error(
                    "E9101",
                    f"Backend '{self.name}' does not support '{capability.value}' "
                    f"required by the plan",
                )
        return bag

    # -- Execution ---------------------------------------------------------

    def can_execute(self, plan: Optional[LogicalPlan] = None) -> bool:
        """Whether this backend can execute a plan.

        Execution additionally requires a configured connection/driver; see
        :meth:`is_available`.
        """
        return self.supports(BackendCapability.EXECUTION)

    def is_available(self) -> bool:
        """Whether the backing driver/connection is ready to execute queries."""
        return False

    # -- Interface ---------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": sorted(c.value for c in self._capabilities),
            "execution_available": self.is_available(),
        }

    def generate(self, plan: LogicalPlan) -> BackendOperation:  # pragma: no cover
        raise NotImplementedError

    def execute(self, plan: LogicalPlan, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError(
            f"Backend '{self.name}' does not support execution"
        )
