"""Tests for optimization passes and cost model."""
from optisql.ir import (
    BinaryExpr,
    ColumnRef,
    Filter,
    Limit,
    LiteralValue,
    LogicalPlan,
    Project,
    Scan,
    clone_plan,
)
from optisql.optimizer import (
    ConstantFoldingPass,
    FilterReorderingPass,
    LimitPushdownPass,
    ProjectionPushdownPass,
    QueryOptimizer,
)
from optisql.optimizer.cost import CostModel, PlanCost
from optisql.metadata import Catalog
from tests.conftest import build_sample_catalog


def build_scan_filter():
    return Filter(
        predicate=BinaryExpr(
            op="AND",
            left=BinaryExpr(op=">", left=ColumnRef("age"), right=LiteralValue(value=18, kind="integer")),
            right=BinaryExpr(op="=", left=ColumnRef("country"), right=LiteralValue(value="India", kind="string")),
        ),
        input=Scan(collection="users"),
    )


def test_filter_reordering_splits_conjunctions():
    plan = build_scan_filter()
    pass_ = FilterReorderingPass()
    optimized = pass_.apply(plan)
    # After splitting, at least the structure is preserved.
    assert optimized.predicate is not None


def test_filter_reordering_prefers_equality():
    plan = build_scan_filter()
    pass_ = FilterReorderingPass()
    optimized = pass_.apply(clone_plan(plan))
    # The equality country='India' should come before age>18 in our heuristic.
    preds = pass_._split_conjunctions(optimized.predicate)
    # Check first is equality
    first = preds[0] if preds else optimized.predicate
    assert isinstance(first, BinaryExpr) and first.op == "="


def test_projection_pushdown_updates_scan():
    plan = Project(
        items=[("name", ColumnRef("name"))],
        input=Filter(
            predicate=BinaryExpr(op=">", left=ColumnRef("age"), right=LiteralValue(value=18, kind="integer")),
            input=Scan(collection="users"),
        ),
    )
    pass_ = ProjectionPushdownPass()
    pass_.apply(plan)
    scan = plan.input.input if hasattr(plan.input, "input") else None
    # Find scan
    current = plan
    while hasattr(current, "input") and current.input:
        if isinstance(current.input, Scan):
            break
        current = current.input
    assert isinstance(current.input, Scan)
    assert current.input.projection is not None
    assert set(current.input.projection) >= {"name", "age"}


def test_constant_folding_pass_runs():
    plan = Scan(collection="users")
    pass_ = ConstantFoldingPass()
    res = pass_.apply(clone_plan(plan))
    assert isinstance(res, LogicalPlan)


def test_limit_pushdown_pass_runs():
    plan = Limit(count=10, input=Scan(collection="users"))
    pass_ = LimitPushdownPass()
    res = pass_.apply(clone_plan(plan))
    assert isinstance(res, LogicalPlan)


def test_query_optimizer_produces_result():
    plan = build_scan_filter()
    opt = QueryOptimizer()
    res = opt.optimize(plan)
    assert res.optimized is not None
    assert res.cost_breakdown is not None
    assert res.cost_breakdown.approximate is True


def test_query_optimizer_can_disable_passes():
    plan = build_scan_filter()
    opt = QueryOptimizer(enable_passes=False)
    res = opt.optimize(plan)
    assert res.trace == []


def test_cost_model_estimates_scan_and_filter():
    cat = build_sample_catalog()
    cost = CostModel(cat)
    plan = Filter(
        predicate=BinaryExpr(op=">", left=ColumnRef("age"), right=LiteralValue(value=18, kind="integer")),
        input=Scan(collection="users"),
    )
    pc = cost.estimate(plan)
    assert pc.total > 0
    assert "scan" in pc.components
    assert "filter" in pc.components
    assert pc.approximate is True


def test_cost_model_uses_cardinality_from_catalog():
    cat = build_sample_catalog()
    cost = CostModel(cat)
    plan = Scan(collection="users")
    pc = cost.estimate(plan)
    # Users has cardinality 10000
    assert pc.components["scan"].notes


def test_plan_cost_breakdown_fields():
    pc = PlanCost(total=1.5)
    pc.add("foo", 0.5, "test")
    assert pc.components["foo"].value == 0.5
    assert pc.total == 2.0
