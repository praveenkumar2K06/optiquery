"""Tests for AST-to-IR lowering and plan utilities."""
from optisql.ir import (
    BinaryExpr,
    ColumnRef,
    Distinct,
    Filter,
    Group,
    Limit,
    LogicalPlan,
    LiteralValue,
    Project,
    Scan,
    Skip,
    Sort,
    clone_plan,
    collect_referenced_fields,
    plan_to_json,
    plan_to_string,
)
from optisql.parser import parse_or_raise
from optisql.planner import lower

from tests.conftest import build_sample_catalog


def build(sql):
    return lower(parse_or_raise(sql))


def test_basic_select_plan_shape(sample_catalog):
    plan = build("SELECT name FROM users WHERE age >= 18 LIMIT 10")
    # Limit -> Project -> Filter -> Scan
    assert isinstance(plan, Limit)
    assert isinstance(plan.input, Project)
    assert isinstance(plan.input.input, Filter)
    assert isinstance(plan.input.input.input, Scan)


def test_plan_scan_collection_and_alias(sample_catalog):
    plan = build("SELECT name FROM users")
    scan = plan.input  # Project input
    assert isinstance(scan, Scan)
    assert scan.collection == "users"
    assert scan.alias == "users"


def test_projection_contains_column_expr(sample_catalog):
    plan = build("SELECT name FROM users")
    assert isinstance(plan, Project)
    name, expr = plan.items[0]
    assert name == "name"
    assert isinstance(expr, ColumnRef)
    assert expr.name == "name"


def test_filter_expression_lowering(sample_catalog):
    plan = build("SELECT * FROM users WHERE age >= 18")
    filt = plan.input  # Project -> Filter
    assert isinstance(filt, Filter)
    assert filt.predicate.op == ">="
    assert isinstance(filt.predicate.left, ColumnRef)
    assert isinstance(filt.predicate.right, LiteralValue)
    assert filt.predicate.right.value == 18


def test_order_by_lowering(sample_catalog):
    plan = build("SELECT name FROM users ORDER BY name DESC")
    # Project -> Sort -> Scan: the sort sits below the projection so sort keys
    # need not be part of the SELECT list.
    assert isinstance(plan, Project)
    assert isinstance(plan.input, Sort)
    expr, desc = plan.input.keys[0]
    assert desc is True
    assert expr.name == "name"


def test_order_by_non_projected_column_keeps_sort_below_projection(sample_catalog):
    plan = build("SELECT name FROM users ORDER BY age")
    assert isinstance(plan, Project)
    assert isinstance(plan.input, Sort)
    key_expr, _ = plan.input.keys[0]
    assert key_expr.name == "age"


def test_order_by_alias_resolves_to_expression(sample_catalog):
    plan = build("SELECT age * 2 AS doubled FROM users ORDER BY doubled")
    assert isinstance(plan, Project)
    assert isinstance(plan.input, Sort)
    key_expr, _ = plan.input.keys[0]
    assert isinstance(key_expr, BinaryExpr)


def test_limit_and_offset_order(sample_catalog):
    plan = build("SELECT name FROM users LIMIT 10 OFFSET 5")
    # Limit sits above Skip
    assert isinstance(plan, Limit)
    assert isinstance(plan.input, Skip)
    assert plan.count == 10
    assert plan.input.count == 5


def test_distinct_lowering(sample_catalog):
    plan = build("SELECT DISTINCT country FROM users")
    assert isinstance(plan, Distinct)


def test_group_by_lowering(sample_catalog):
    plan = build("SELECT country, COUNT(*) FROM users GROUP BY country")
    # Project -> Group -> Scan
    assert isinstance(plan, Project)
    assert isinstance(plan.input, Group)
    group = plan.input
    assert len(group.group_keys) == 1
    assert any(name == "COUNT" or name.lower() == "count" for name, _ in group.aggregates)


def test_having_lowering(sample_catalog):
    plan = build(
        "SELECT country, COUNT(*) FROM users GROUP BY country HAVING COUNT(*) > 5"
    )
    # Project -> Filter(having) -> Group -> Scan
    assert isinstance(plan, Project)
    assert isinstance(plan.input, Filter)
    assert isinstance(plan.input.input, Group)


def test_clone_plan_is_independent(sample_catalog):
    plan = build("SELECT name FROM users WHERE age > 5")
    cloned = clone_plan(plan)
    assert plan is not cloned
    assert plan_to_string(plan) == plan_to_string(cloned)


def test_collect_referenced_fields(sample_catalog):
    plan = build("SELECT name FROM users WHERE age > 5 AND country = 'IN'")
    filt = plan.input
    fields = collect_referenced_fields(filt.predicate)
    assert fields == {"age", "country"}


def test_plan_to_string_contains_operators(sample_catalog):
    plan = build("SELECT name FROM users WHERE age > 5 LIMIT 3")
    text = plan_to_string(plan)
    assert "Limit" in text
    assert "Project" in text
    assert "Filter" in text
    assert "Scan" in text


def test_plan_to_json_serializable(sample_catalog):
    import json

    plan = build("SELECT name FROM users WHERE age > 5 LIMIT 3")
    data = plan_to_json(plan)
    text = json.dumps(data)
    assert "Scan" in text
    assert "Filter" in text


def test_insert_lowering(sample_catalog):
    plan = build("INSERT INTO users (name, age) VALUES ('A', 1)")
    from optisql.ir import Insert

    assert isinstance(plan, Insert)
    assert plan.collection == "users"
    assert plan.columns == ["name", "age"]


def test_update_lowering(sample_catalog):
    plan = build("UPDATE users SET age = 30 WHERE name = 'A'")
    from optisql.ir import Update

    assert isinstance(plan, Update)
    assert plan.assignments[0][0] == "age"


def test_delete_lowering(sample_catalog):
    plan = build("DELETE FROM users WHERE age < 18")
    from optisql.ir import Delete

    assert isinstance(plan, Delete)
    assert plan.predicate is not None
