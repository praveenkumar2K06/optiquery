"""Query optimizer with optimization passes and cost model."""
from .passes.filter import FilterReorderingPass
from .passes.projection import ProjectionPushdownPass
from .passes.constant import ConstantFoldingPass
from .passes.cse import CommonSubexpressionEliminationPass
from .passes.limit import LimitPushdownPass
from .optimizer import QueryOptimizer, OptimizationResult
from .manager import PassManager

__all__ = [
    "FilterReorderingPass",
    "ProjectionPushdownPass",
    "ConstantFoldingPass",
    "CommonSubexpressionEliminationPass",
    "LimitPushdownPass",
    "QueryOptimizer",
    "OptimizationResult",
    "PassManager",
]
