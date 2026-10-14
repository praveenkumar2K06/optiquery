"""Constant folding and algebraic simplification pass."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from ...ir import (
    BinaryExpr,
    IRExpr,
    LiteralValue,
    UnaryExpr,
)
from ...ir.transform import clone_plan
from ...metadata import FieldType


class ConstantFoldingPass:
    """Evaluate constant subexpressions where safe."""

    name = "constant_folding"

    def __init__(self):
        self.changes = 0

    def apply(self, plan) -> "LogicalPlan":  # type: ignore[name-defined]
        from ...ir import LogicalPlan

        self.changes = 0
        cloned = clone_plan(plan)
        return self._rewrite(cloned)

    def _rewrite(self, node):  # type: ignore[no-untyped-def]
        for child in node.inputs():
            self._rewrite(child)
        # Rewrite predicates on Filter/Project expressions if needed.
        # Simplified: just mark no-op for now by visiting structure.
        return node
