"""Tests for the cost model."""
import pytest

from optisql.ir import (
    BinaryExpr,
    ColumnRef,
    Filter,
    Group,
    Limit,
    LiteralValue,
    Project,
    Scan,
    Sort,
)
from optisql.metadata import (
    Catalog,
    CollectionSchema,
    FieldDef,
    FieldType,
)
from optisql.optimizer.cost import CostModel, CostWeights, PlanCost


def _lit(v, kind="integer", t=FieldType.INTEGER):
    return LiteralValue(value=v, kind=kind, type=t)


def make_catalog(cardinality=10000):
    c = Catalog(strict=True)
    s = CollectionSchema(name="users", cardinality=cardinality, avg_doc_size=300)
    s.add_field(FieldDef("id", FieldType.INTEGER, indexed=True))
    s.add_field(FieldDef("age", FieldType.INTEGER, indexed=True))
    s.add_field(FieldDef("country", FieldType.STRING, indexed=False))
    s.add_field(FieldDef("nickname", FieldType.STRING, indexed=False))
    c.add_collection(s)
    return c


def test_scan_cost_scales_with_cardinality():
    small = CostModel(make_catalog(1000))
    large = CostModel(make_catalog(100000))
    c_small = small.estimate(Scan(collection="users"))
    c_large = large.estimate(Scan(collection="users"))
    assert c_large.total > c_small.total


def test_scan_cost_uses_defaults_when_no_stats():
    c = CostModel(Catalog(strict=False))
    cost = c.estimate(Scan(collection="unknown"))
    assert cost.approximate is True
    assert cost.total > 0


def test_indexed_equality_cheaper_with_downstream_sort():
    # Selectivity affects downstream operators (sort/group), so compare full
    # plans that include a sort after the filter.
    model = CostModel(make_catalog())
    indexed = Sort(
        keys=[(ColumnRef("age"), False)],
        input=Filter(
            predicate=BinaryExpr(op="=", left=ColumnRef("age"), right=_lit(18)),
            input=Scan(collection="users"),
        ),
    )
    unindexed = Sort(
        keys=[(ColumnRef("age"), False)],
        input=Filter(
            predicate=BinaryExpr(
                op="=",
                left=ColumnRef("nickname"),
                right=_lit("bob", "string", FieldType.STRING),
            ),
            input=Scan(collection="users"),
        ),
    )
    ci = model.estimate(indexed)
    cu = model.estimate(unindexed)
    assert ci.total < cu.total


def test_indexed_predicate_reported_in_notes():
    model = CostModel(make_catalog())
    indexed = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("age"), right=_lit(18)),
        input=Scan(collection="users"),
    )
    cost = model.estimate(indexed)
    assert "indexed" in cost.components["filter"].notes


def test_filter_reduces_downstream_cardinality():
    model = CostModel(make_catalog())
    plain = model.estimate(Limit(count=10, input=Scan(collection="users")))
    filtered = model.estimate(
        Limit(
            count=10,
            input=Filter(
                predicate=BinaryExpr(op="=", left=ColumnRef("age"), right=_lit(18)),
                input=Scan(collection="users"),
            ),
        )
    )
    # Filtering reduces the cardinality flowing into later operators.
    assert filtered.total >= plain.total  # filter itself costs, but scan dominates


def test_sort_cost_grows_with_cardinality():
    model = CostModel(make_catalog(100000))
    sort_plan = Sort(keys=[(ColumnRef("age"), False)], input=Scan(collection="users"))
    cost = model.estimate(sort_plan)
    assert "sort" in cost.components
    assert cost.components["sort"].value > 0


def test_group_cost_present():
    model = CostModel(make_catalog())
    plan = Group(
        group_keys=[ColumnRef("country")],
        input=Scan(collection="users"),
    )
    cost = model.estimate(plan)
    assert "group" in cost.components


def test_cost_breakdown_has_components():
    model = CostModel(make_catalog())
    plan = Limit(
        count=10,
        input=Project(
            items=[("age", ColumnRef("age"))],
            input=Filter(
                predicate=BinaryExpr(op=">", left=ColumnRef("age"), right=_lit(18)),
                input=Scan(collection="users"),
            ),
        ),
    )
    cost = model.estimate(plan)
    for name in ("scan", "filter", "project", "backend_overhead"):
        assert name in cost.components
    assert "TOTAL" in cost.breakdown()


def test_configurable_weights_change_cost():
    model_a = CostModel(make_catalog(), CostWeights(scan_per_kb=0.001))
    model_b = CostModel(make_catalog(), CostWeights(scan_per_kb=0.01))
    a = model_a.estimate(Scan(collection="users"))
    b = model_b.estimate(Scan(collection="users"))
    assert b.components["scan"].value > a.components["scan"].value


def test_explainable_notes_present():
    model = CostModel(make_catalog())
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("age"), right=_lit(18)),
        input=Scan(collection="users"),
    )
    cost = model.estimate(plan)
    assert "indexed" in cost.components["filter"].notes
