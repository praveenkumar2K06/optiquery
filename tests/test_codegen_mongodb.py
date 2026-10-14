"""Tests for MongoDB aggregation pipeline generation."""
from optisql.codegen.mongodb import MongoDBGenerator, generate_mongodb
from optisql.ir import (
    BinaryExpr,
    ColumnRef,
    Distinct,
    Filter,
    FunctionExpr,
    Group,
    Limit,
    LiteralValue,
    Project,
    Scan,
    Skip,
    Sort,
)
from optisql.metadata import FieldType


def _lit(v, kind="integer"):
    t = FieldType.STRING if kind == "string" else FieldType.INTEGER
    return LiteralValue(value=v, kind=kind, type=t)


def test_simple_match_pipeline():
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("country"), right=_lit("India", "string")),
        input=Scan(collection="users"),
    )
    result = generate_mongodb(plan)
    assert result.collection == "users"
    # $match uses the query-document form, not an aggregation expression.
    assert result.pipeline[0] == {"$match": {"country": "India"}}


def test_comparison_operators_map():
    ops = {"!=": "$ne", "<": "$lt", ">": "$gt", "<=": "$lte", ">=": "$gte"}
    for sql_op, mongo_op in ops.items():
        plan = Filter(
            predicate=BinaryExpr(op=sql_op, left=ColumnRef("age"), right=_lit(18)),
            input=Scan(collection="users"),
        )
        pipeline = generate_mongodb(plan).pipeline
        match = pipeline[0]["$match"]
        assert match["age"][mongo_op] == 18
    # Equality uses the shorthand query form.
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("age"), right=_lit(18)),
        input=Scan(collection="users"),
    )
    assert generate_mongodb(plan).pipeline[0]["$match"] == {"age": 18}


def test_and_or_mapping():
    plan = Filter(
        predicate=BinaryExpr(
            op="AND",
            left=BinaryExpr(op=">", left=ColumnRef("age"), right=_lit(18)),
            right=BinaryExpr(op="=", left=ColumnRef("country"), right=_lit("IN", "string")),
        ),
        input=Scan(collection="users"),
    )
    match = generate_mongodb(plan).pipeline[0]["$match"]
    assert "$and" in match
    assert {"age": {"$gt": 18}} in match["$and"]
    assert {"country": "IN"} in match["$and"]


def test_projection_produces_dollar_field_refs():
    plan = Project(
        items=[("name", ColumnRef("name"))],
        input=Scan(collection="users"),
    )
    pipeline = generate_mongodb(plan).pipeline
    assert {"$project": {"name": "$name"}} in pipeline


def test_sort_pipeline():
    plan = Sort(keys=[(ColumnRef("name"), False)], input=Scan(collection="users"))
    pipeline = generate_mongodb(plan).pipeline
    assert {"$sort": {"name": 1}} in pipeline


def test_sort_descending():
    plan = Sort(keys=[(ColumnRef("age"), True)], input=Scan(collection="users"))
    pipeline = generate_mongodb(plan).pipeline
    assert {"$sort": {"age": -1}} in pipeline


def test_limit_and_skip_pipeline():
    plan = Limit(count=10, input=Skip(count=5, input=Scan(collection="users")))
    pipeline = generate_mongodb(plan).pipeline
    assert {"$limit": 10} in pipeline
    assert {"$skip": 5} in pipeline


def test_group_with_count():
    plan = Group(
        group_keys=[ColumnRef("country")],
        aggregates=[
            ("count", FunctionExpr(name="COUNT", args=(), aggregate=True, type=FieldType.INTEGER))
        ],
        input=Scan(collection="users"),
    )
    pipeline = generate_mongodb(plan).pipeline
    group = pipeline[0]["$group"]
    assert group["_id"] == "$country"
    assert group["count"] == {"$sum": 1}


def test_group_with_avg_sum():
    plan = Group(
        group_keys=[],
        aggregates=[
            ("avg_age", FunctionExpr(name="AVG", args=(ColumnRef("age"),), aggregate=True)),
            ("total", FunctionExpr(name="SUM", args=(ColumnRef("total"),), aggregate=True)),
        ],
        input=Scan(collection="users"),
    )
    group = generate_mongodb(plan).pipeline[0]["$group"]
    assert group["_id"] is None
    assert group["avg_age"] == {"$avg": "$age"}
    assert group["total"] == {"$sum": "$total"}


def test_full_pipeline_order():
    plan = Limit(
        count=10,
        input=Sort(
            keys=[(ColumnRef("name"), False)],
            input=Project(
                items=[("name", ColumnRef("name"))],
                input=Filter(
                    predicate=BinaryExpr(
                        op=">=", left=ColumnRef("age"), right=_lit(18)
                    ),
                    input=Scan(collection="users"),
                ),
            ),
        ),
    )
    pipeline = generate_mongodb(plan).pipeline
    assert "$match" in pipeline[0]
    assert "$project" in pipeline[1]
    assert "$sort" in pipeline[2]
    assert "$limit" in pipeline[3]


def test_unsupported_operator_reports_error():
    from optisql.ir import Insert

    result = generate_mongodb(Insert(collection="users", columns=["a"], rows=[[]]))
    assert result.diagnostics.has_errors
    assert any(d.code == "E7001" for d in result.diagnostics.errors)


def test_inequality_and_null_handling():
    plan = Filter(
        predicate=BinaryExpr(
            op="!=", left=ColumnRef("status"), right=_lit("closed", "string")
        ),
        input=Scan(collection="orders"),
    )
    match = generate_mongodb(plan).pipeline[0]["$match"]
    assert match == {"status": {"$ne": "closed"}}


def test_distinct_groups_by_projected_fields():
    plan = Distinct(
        input=Project(
            items=[("country", ColumnRef("country")), ("age", ColumnRef("age"))],
            input=Scan(collection="users"),
        )
    )
    pipeline = generate_mongodb(plan).pipeline
    group = next(s["$group"] for s in pipeline if "$group" in s)
    assert group["_id"] == {"country": "$country", "age": "$age"}
    final = pipeline[-1]["$project"]
    assert final == {"_id": 0, "country": "$_id.country", "age": "$_id.age"}


def test_distinct_star_is_rejected_not_wrong():
    plan = Distinct(input=Scan(collection="users"))
    result = generate_mongodb(plan)
    assert result.diagnostics.has_errors
    assert any(d.code == "E7004" for d in result.diagnostics.errors)
    # No misleading pipeline is produced.
    assert result.pipeline == []
