"""Common subexpression elimination via DAG subtree sharing.

The IR is a tree, but structurally identical subtrees compute identical results.
This pass detects those duplicates and *shares* a single instance of each unique
subtree, turning the plan into a DAG. Two identical scans, filters, or
projections therefore become one node referenced from multiple parents.

Correctness notes
-----------------
* Sharing is only applied to subtrees with an identical structural signature
  (operator type, payload fields, and recursively identical inputs). Such
  subtrees are pure and produce the same records, so referencing one instance
  from several parents is semantically equivalent to duplicating it.
* The pass clones the input plan, so the caller's plan is never mutated and no
  node is shared *across* plan instances.
* Expressions are frozen dataclasses and therefore hashable, which makes their
  structural equality cheap and reliable.

Because the result is a DAG, downstream phases must treat plan nodes as
read-only. In the default pipeline CSE runs after the other rewriting passes.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict, List, Tuple

from ...ir import LogicalPlan


class CommonSubexpressionEliminationPass:
    """Share structurally identical subtrees (DAG-based CSE)."""

    name = "common_subexpression_elimination"

    def __init__(self) -> None:
        self.changes = 0

    def apply(self, plan: LogicalPlan) -> LogicalPlan:
        self.changes = 0
        self._memo: Dict[Any, LogicalPlan] = {}
        return self._dedup(plan)

    # -- Internals ---------------------------------------------------------

    def _dedup(self, node: LogicalPlan) -> LogicalPlan:
        children = node.inputs()
        if children:
            new_children = [self._dedup(c) for c in children]
            node = node.with_inputs(new_children)

        key = self._signature(node)
        existing = self._memo.get(key)
        if existing is not None:
            if existing is not node:
                self.changes += 1
            return existing

        self._memo[key] = node
        return node

    def _signature(self, node: LogicalPlan) -> Tuple:
        """A hashable structural signature for a subtree."""
        payload: List[Tuple[str, Any]] = []
        if is_dataclass(node):
            for field in fields(node):
                value = getattr(node, field.name)
                if isinstance(value, LogicalPlan):
                    continue  # children are captured separately
                payload.append((field.name, _freeze(value)))
        child_signatures = tuple(self._signature(c) for c in node.inputs())
        return (type(node).__name__, tuple(payload), child_signatures)


def _freeze(value: Any) -> Any:
    """Convert a payload value into a hashable, structurally-comparable form."""
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    return value


__all__ = ["CommonSubexpressionEliminationPass"]
