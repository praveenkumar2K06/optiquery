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


def test_limit_zero_is_always_false_not_invalid_limit():
    plan = Limit(count=0, input=Scan(collection="users"))
    pipeline = generate_mongodb(plan).pipeline
    # MongoDB $limit requires a positive integer, so LIMIT 0 must not emit
    # {"$limit": 0}.
    assert {"$limit": 0} not in pipeline
    assert pipeline == [{"$match": {"$expr": {"$eq": [1, 0]}}}]


def test_like_escapes_regex_metacharacters():
    from optisql.ir import LikeExpr

    plan = Filter(
        predicate=LikeExpr(
            operand=ColumnRef("name"),
            pattern=_lit("a.b%", "string"),
        ),
        input=Scan(collection="users"),
    )
    match = generate_mongodb(plan).pipeline[0]["$match"]
    assert match == {"name": {"$regex": "^a\\.b.*$"}}


def test_like_translates_wildcards():
    from optisql.ir import LikeExpr

    plan = Filter(
        predicate=LikeExpr(operand=ColumnRef("name"), pattern=_lit("_x%", "string")),
        input=Scan(collection="users"),
    )
    match = generate_mongodb(plan).pipeline[0]["$match"]
    assert match == {"name": {"$regex": "^.x.*$"}}


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


def _project_expr(name, expr):
    return Project(items=[(name, expr)], input=Scan(collection="users"))


def test_scalar_function_translation():
    from optisql.ir import FunctionExpr

    cases = {
        "ABS": {"$abs": "$age"},
        "LOWER": {"$toLower": "$name"},
        "UPPER": {"$toUpper": "$name"},
        "LENGTH": {"$strLenCP": "$name"},
    }
    for fn, expected in cases.items():
        expr = FunctionExpr(
            name=fn, args=(ColumnRef("age" if fn == "ABS" else "name"),), aggregate=False
        )
        pipeline = generate_mongodb(_project_expr("out", expr)).pipeline
        assert pipeline[0]["$project"]["out"] == expected


def test_round_and_coalesce_translation():
    from optisql.ir import FunctionExpr

    rounded = FunctionExpr(
        name="ROUND",
        args=(ColumnRef("total"), LiteralValue(value=2, kind="integer")),
        aggregate=False,
    )
    pipeline = generate_mongodb(_project_expr("r", rounded)).pipeline
    assert pipeline[0]["$project"]["r"] == {"$round": ["$total", 2]}

    coalesced = FunctionExpr(
        name="COALESCE",
        args=(ColumnRef("email"), _lit("none", "string")),
        aggregate=False,
    )
    pipeline = generate_mongodb(_project_expr("e", coalesced)).pipeline
    assert pipeline[0]["$project"]["e"] == {"$ifNull": ["$email", "none"]}


def test_case_expression_translation():
    from optisql.ir import CaseExpr, CaseWhen

    case = CaseExpr(
        whens=(
            CaseWhen(
                condition=BinaryExpr(
                    op=">=", left=ColumnRef("age"), right=_lit(18)
                ),
                result=_lit("adult", "string"),
            ),
        ),
        default=_lit("minor", "string"),
    )
    pipeline = generate_mongodb(_project_expr("kind", case)).pipeline
    assert pipeline[0]["$project"]["kind"] == {
        "$cond": [{"$gte": ["$age", 18]}, "adult", "minor"]
    }


def test_scalar_function_in_predicate_uses_expr():
    from optisql.ir import FunctionExpr

    plan = Filter(
        predicate=BinaryExpr(
            op="=",
            left=FunctionExpr(name="UPPER", args=(ColumnRef("country"),)),
            right=_lit("INDIA", "string"),
        ),
        input=Scan(collection="users"),
    )
    match = generate_mongodb(plan).pipeline[0]["$match"]
    assert match == {"$expr": {"$eq": [{"$toUpper": "$country"}, "INDIA"]}}


def test_computed_rhs_comparison_uses_expr_not_query_document():
    # age > age * 2 has a computed right-hand side, so it must use $expr rather
    # than a query-document operator whose operand would be misinterpreted.
    plan = Filter(
        predicate=BinaryExpr(
            op=">",
            left=ColumnRef("age"),
            right=BinaryExpr(op="*", left=ColumnRef("age"), right=_lit(2)),
        ),
        input=Scan(collection="users"),
    )
    match = generate_mongodb(plan).pipeline[0]["$match"]
    assert match == {"$expr": {"$gt": ["$age", {"$multiply": ["$age", 2]}]}}


def test_literal_rhs_comparison_uses_query_document():
    plan = Filter(
        predicate=BinaryExpr(op=">=", left=ColumnRef("age"), right=_lit(18)),
        input=Scan(collection="users"),
    )
    match = generate_mongodb(plan).pipeline[0]["$match"]
    assert match == {"age": {"$gte": 18}}


def test_computed_sort_key_is_materialized_and_removed():
    plan = Project(
        items=[("doubled", BinaryExpr(op="*", left=ColumnRef("age"), right=_lit(2)))],
        input=Sort(
            keys=[
                (
                    BinaryExpr(op="*", left=ColumnRef("age"), right=_lit(2)),
                    False,
                )
            ],
            input=Scan(collection="users"),
        ),
    )
    pipeline = generate_mongodb(plan).pipeline
    # A temporary field is created, sorted on, and then removed.
    assert "$addFields" in pipeline[0]
    temp = next(iter(pipeline[0]["$addFields"]))
    assert pipeline[1] == {"$sort": {temp: 1}}
    assert pipeline[2] == {"$project": {temp: 0}}
    # The final projection exposes only the requested output field.
    assert pipeline[-1] == {"$project": {"doubled": {"$multiply": ["$age", 2]}}}


def test_sort_on_plain_column_adds_no_temp_field():
    plan = Project(
        items=[("name", ColumnRef("name"))],
        input=Sort(keys=[(ColumnRef("age"), True)], input=Scan(collection="users")),
    )
    pipeline = generate_mongodb(plan).pipeline
    assert pipeline == [{"$sort": {"age": -1}}, {"$project": {"name": "$name"}}]


def test_group_by_restores_key_as_named_field():
    plan = Project(
        items=[("country", ColumnRef("country")), ("n", ColumnRef("n"))],
        input=Group(
            group_keys=[ColumnRef("country")],
            aggregates=[("n", FunctionExpr(name="COUNT", args=(ColumnRef("*"),)))],
            input=Scan(collection="users"),
        ),
    )
    pipeline = generate_mongodb(plan).pipeline
    assert pipeline[0] == {"$group": {"_id": "$country", "n": {"$sum": 1}}}
    # The key is restored from _id, and the aggregate output is preserved.
    assert pipeline[1] == {"$project": {"_id": 0, "country": "$_id", "n": 1}}


def test_group_by_multiple_keys_restore_paths():
    plan = Group(
        group_keys=[ColumnRef("country"), ColumnRef("age")],
        aggregates=[],
        input=Scan(collection="users"),
    )
    pipeline = generate_mongodb(plan).pipeline
    assert pipeline[0]["$group"]["_id"] == {"country": "$country", "age": "$age"}
    assert pipeline[1] == {
        "$project": {"_id": 0, "country": "$_id.country", "age": "$_id.age"}
    }


def test_count_distinct_uses_add_to_set():
    plan = Group(
        aggregates=[
            (
                "c",
                FunctionExpr(
                    name="COUNT",
                    args=(ColumnRef("country"),),
                    distinct=True,
                ),
            )
        ],
        input=Scan(collection="users"),
    )
    pipeline = generate_mongodb(plan).pipeline
    assert pipeline[0]["$group"]["__c_distinct"] == {"$addToSet": "$country"}
    assert pipeline[1]["$project"]["c"] == {"$size": "$__c_distinct"}


def test_sum_distinct_uses_add_to_set():
    plan = Group(
        aggregates=[
            (
                "s",
                FunctionExpr(name="SUM", args=(ColumnRef("age"),), distinct=True),
            )
        ],
        input=Scan(collection="users"),
    )
    pipeline = generate_mongodb(plan).pipeline
    assert pipeline[0]["$group"]["__s_distinct"] == {"$addToSet": "$age"}
    assert pipeline[1]["$project"]["s"] == {"$sum": "$__s_distinct"}
