"""Tests for Redis command generation and its data-model limitations."""
from optisql.codegen.redis import RedisDataModel, RedisGenerator, generate_redis
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
from optisql.metadata import FieldType


def _lit(v, kind="string"):
    return LiteralValue(value=v, kind=kind, type=FieldType.STRING)


def point_lookup_plan():
    return Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("id"), right=_lit("42")),
        input=Scan(collection="users"),
    )


def test_primary_key_point_lookup():
    result = generate_redis(point_lookup_plan())
    assert result.supported
    assert result.commands[0]["command"] == "HGETALL"
    assert result.commands[0]["args"] == ["optisql:users:42"]


def test_projection_becomes_hmget():
    plan = Project(
        items=[("name", ColumnRef("name")), ("age", ColumnRef("age"))],
        input=point_lookup_plan(),
    )
    result = generate_redis(plan)
    assert result.commands[0]["command"] == "HMGET"
    assert result.commands[0]["args"] == ["optisql:users:42", "name", "age"]


def test_indexed_field_lookup():
    model = RedisDataModel(indexed_fields={"users": ["email"]})
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("email"), right=_lit("a@b.com")),
        input=Scan(collection="users"),
    )
    result = generate_redis(plan, model)
    assert result.supported
    assert result.commands[0]["command"] == "SMEMBERS"
    assert result.commands[0]["args"] == ["optisql:idx:users:email:a@b.com"]


def test_unindexed_field_is_rejected():
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("nickname"), right=_lit("bob")),
        input=Scan(collection="users"),
    )
    result = generate_redis(plan)
    assert not result.supported
    assert any(d.code == "E8006" for d in result.diagnostics.errors)


def test_range_predicate_is_rejected():
    plan = Filter(
        predicate=BinaryExpr(op=">", left=ColumnRef("age"), right=_lit("18", "integer")),
        input=Scan(collection="users"),
    )
    result = generate_redis(plan)
    assert not result.supported
    assert any(d.code in ("E8003",) for d in result.diagnostics.errors)


def test_sort_is_rejected():
    plan = Sort(keys=[(ColumnRef("name"), False)], input=point_lookup_plan())
    result = generate_redis(plan)
    assert not result.supported
    assert any(d.code == "E8001" for d in result.diagnostics.errors)


def test_group_is_rejected():
    plan = Group(group_keys=[ColumnRef("country")], input=Scan(collection="users"))
    result = generate_redis(plan)
    assert not result.supported


def test_limit_is_rejected():
    plan = Limit(count=5, input=point_lookup_plan())
    result = generate_redis(plan)
    assert not result.supported


def test_compound_predicate_is_rejected():
    plan = Filter(
        predicate=BinaryExpr(
            op="AND",
            left=BinaryExpr(op="=", left=ColumnRef("id"), right=_lit("1")),
            right=BinaryExpr(op="=", left=ColumnRef("email"), right=_lit("a@b.com")),
        ),
        input=Scan(collection="users"),
    )
    result = generate_redis(plan)
    assert not result.supported


def test_scan_without_predicate_is_rejected():
    result = generate_redis(Scan(collection="users"))
    assert not result.supported


def test_reversed_equality_operands():
    plan = Filter(
        predicate=BinaryExpr(op="=", left=_lit("42"), right=ColumnRef("id")),
        input=Scan(collection="users"),
    )
    result = generate_redis(plan)
    assert result.supported
    assert result.commands[0]["args"] == ["optisql:users:42"]


def test_custom_key_prefix_and_primary_key():
    model = RedisDataModel(key_prefix="app", primary_key="user_id")
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("user_id"), right=_lit("7", "integer")),
        input=Scan(collection="users"),
    )
    result = generate_redis(plan, model)
    assert result.commands[0]["args"] == ["app:users:7"]
