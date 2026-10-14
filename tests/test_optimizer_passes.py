"""Focused tests for individual optimization passes."""
from optisql.ir import (
    BinaryExpr,
    ColumnRef,
    Filter,
    LiteralValue,
    Project,
    Scan,
    UnaryExpr,
    plan_to_string,
)
from optisql.ir.serialize import expr_to_string
from optisql.metadata import FieldType
from optisql.optimizer.passes.constant import ConstantFoldingPass
from optisql.optimizer.passes.filter import FilterReorderingPass
from optisql.optimizer.passes.projection import ProjectionPushdownPass


def lit(v, kind="integer"):
    t = {"integer": FieldType.INTEGER, "decimal": FieldType.DECIMAL,
         "string": FieldType.STRING, "boolean": FieldType.BOOLEAN}.get(kind, FieldType.ANY)
    return LiteralValue(value=v, kind=kind, type=t)


# -- Constant folding ------------------------------------------------------


def test_fold_constant_arithmetic():
    p = ConstantFoldingPass()
    expr = BinaryExpr(op="+", left=lit(2), right=BinaryExpr(op="*", left=lit(3), right=lit(4)))
    folded = p._fold(expr)
    assert isinstance(folded, LiteralValue)
    assert folded.value == 14


def test_fold_constant_comparison():
    p = ConstantFoldingPass()
    expr = BinaryExpr(op=">", left=lit(5), right=lit(3))
    folded = p._fold(expr)
    assert isinstance(folded, LiteralValue) and folded.value is True


def test_no_division_by_zero_fold():
    p = ConstantFoldingPass()
    expr = BinaryExpr(op="/", left=lit(5), right=lit(0))
    folded = p._fold(expr)
    # Should not fold; must remain a binary expression.
    assert isinstance(folded, BinaryExpr)


def test_null_propagation_in_arithmetic():
    p = ConstantFoldingPass()
    expr = BinaryExpr(op="+", left=LiteralValue(value=None, kind="null", type=FieldType.NULL), right=lit(1))
    folded = p._fold(expr)
    assert isinstance(folded, LiteralValue)
    assert folded.kind == "null"


def test_boolean_identity_and():
    p = ConstantFoldingPass()
    expr = BinaryExpr(op="AND", left=lit(True, "boolean"), right=ColumnRef("active"))
    folded = p._fold(expr)
    assert isinstance(folded, ColumnRef)


def test_boolean_short_circuit_or_true():
    p = ConstantFoldingPass()
    expr = BinaryExpr(op="OR", left=lit(True, "boolean"), right=ColumnRef("x"))
    folded = p._fold(expr)
    assert isinstance(folded, LiteralValue) and folded.value is True


def test_double_negation():
    p = ConstantFoldingPass()
    expr = UnaryExpr(op="NOT", operand=UnaryExpr(op="NOT", operand=ColumnRef("flag")))
    folded = p._fold(expr)
    assert isinstance(folded, ColumnRef)


def test_filter_removed_when_predicate_true():
    plan = Filter(predicate=lit(True, "boolean"), input=Project(
        items=[("x", ColumnRef("x"))], input=Scan(collection="t")))
    p = ConstantFoldingPass()
    result = p.apply(plan)
    # Top-level filter should be removed, leaving the project.
    assert isinstance(result, Project)


def test_is_null_constant_folding():
    from optisql.ir import IsNullExpr

    p = ConstantFoldingPass()
    expr = IsNullExpr(operand=LiteralValue(value=None, kind="null", type=FieldType.NULL), negated=False)
    folded = p._fold(expr)
    assert isinstance(folded, LiteralValue) and folded.value is True


# -- Filter reordering -----------------------------------------------------


def test_filter_reorder_moves_equality_first():
    plan = Filter(
        predicate=BinaryExpr(
            op="AND",
            left=BinaryExpr(op=">", left=ColumnRef("age"), right=lit(18)),
            right=BinaryExpr(op="=", left=ColumnRef("country"), right=lit("IN", "string")),
        ),
        input=Scan(collection="users"),
    )
    p = FilterReorderingPass()
    result = p.apply(plan)
    parts = p._split_conjunctions(result.predicate)
    assert parts[0].op == "="


def test_filter_reorder_is_semantics_preserving():
    plan = Filter(
        predicate=BinaryExpr(
            op="AND",
            left=BinaryExpr(op=">", left=ColumnRef("age"), right=lit(18)),
            right=BinaryExpr(op="=", left=ColumnRef("country"), right=lit("IN", "string")),
        ),
        input=Scan(collection="users"),
    )
    p = FilterReorderingPass()
    result = p.apply(plan)
    from optisql.ir import collect_referenced_fields

    assert collect_referenced_fields(result.predicate) == {"age", "country"}


# -- Projection pushdown ---------------------------------------------------


def test_pushdown_populates_scan_projection():
    plan = Project(
        items=[("name", ColumnRef("name"))],
        input=Filter(
            predicate=BinaryExpr(op=">", left=ColumnRef("age"), right=lit(18)),
            input=Scan(collection="users"),
        ),
    )
    p = ProjectionPushdownPass()
    p.apply(plan)
    assert set(plan.input.input.projection) == {"name", "age"}


def test_pushdown_disabled_for_select_star():
    plan = Project(
        items=[("*", LiteralValue(value="*", kind="string"))],
        input=Filter(
            predicate=BinaryExpr(op=">", left=ColumnRef("age"), right=lit(18)),
            input=Scan(collection="users"),
        ),
    )
    p = ProjectionPushdownPass()
    p.apply(plan)
    assert plan.input.input.projection is None


def test_pushdown_includes_group_and_sort_fields():
    from optisql.ir import Group, Sort

    plan = Sort(
        keys=[(ColumnRef("total"), True)],
        input=Project(
            items=[("country", ColumnRef("country"))],
            input=Group(
                group_keys=[ColumnRef("country")],
                input=Scan(collection="orders"),
            ),
        ),
    )
    p = ProjectionPushdownPass()
    p.apply(plan)
    # Find the scan and check fields
    scan = plan.input.input.input
    assert isinstance(scan, Scan)
    assert "total" in scan.projection
    assert "country" in scan.projection
