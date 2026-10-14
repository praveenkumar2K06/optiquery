"""End-to-end integration tests: SQL text -> compiler pipeline -> backend query.

These tests exercise the whole pipeline (lexer, parser, semantic analysis,
lowering, optimization, codegen) plus the CLI, as opposed to the focused unit
tests for individual phases.
"""
import json

import pytest

from optisql.cli.main import main as cli_main
from optisql.compiler import Compiler
from optisql.metadata import Catalog


@pytest.fixture
def compiler(sample_catalog):
    def _make(backend="mongodb", optimizations=True):
        return Compiler(
            catalog=sample_catalog,
            backend=backend,
            enable_optimizations=optimizations,
        )

    return _make


# -- MongoDB, full pipeline -------------------------------------------------


def test_select_filter_sort_limit(compiler):
    result = compiler().compile(
        "SELECT name, age FROM users WHERE age >= 18 ORDER BY age DESC LIMIT 5"
    )
    assert result.ok
    pipeline = result.generated
    stages = [next(iter(stage)) for stage in pipeline]
    assert stages[0] == "$match"
    assert pipeline[0] == {"$match": {"age": {"$gte": 18}}}
    assert {"$sort": {"age": -1}} in pipeline
    assert {"$limit": 5} in pipeline
    # Sort must precede limit for the ordering to be meaningful.
    assert stages.index("$sort") < stages.index("$limit")
    # Projection is the final output shape.
    assert pipeline[-1] == {"$project": {"name": "$name", "age": "$age"}}


def test_select_star_does_not_narrow_projection(compiler):
    result = compiler().compile("SELECT * FROM users WHERE age > 30")
    assert result.ok
    # No $project stage should be inserted for SELECT *.
    assert all("$project" not in stage for stage in result.generated)
    assert result.generated[0] == {"$match": {"age": {"$gt": 30}}}


def test_group_by_with_having(compiler):
    result = compiler().compile(
        "SELECT country, COUNT(*) AS n FROM users GROUP BY country HAVING COUNT(*) > 5"
    )
    assert result.ok
    group = next(s["$group"] for s in result.generated if "$group" in s)
    assert group["_id"] == "$country"
    assert group["n"] == {"$sum": 1}
    # HAVING becomes a $match on the aggregate output.
    assert any(s.get("$match", {}).get("n") == {"$gt": 5} for s in result.generated)


def test_distinct_is_semantically_correct(compiler):
    result = compiler().compile("SELECT DISTINCT country FROM users")
    assert result.ok
    pipeline = result.generated
    group = next(s["$group"] for s in pipeline if "$group" in s)
    assert group["_id"] == {"country": "$country"}
    final = pipeline[-1]["$project"]
    assert final["_id"] == 0
    assert final["country"] == "$_id.country"
    assert not result.diagnostics.warnings


def test_distinct_star_is_rejected(compiler):
    result = compiler().compile("SELECT DISTINCT * FROM users")
    assert not result.ok
    assert any(d.code == "E7004" for d in result.diagnostics.items)


def test_between_and_in_predicates(compiler):
    result = compiler().compile(
        "SELECT name FROM users WHERE age BETWEEN 18 AND 30 AND country IN ('India','US')"
    )
    assert result.ok
    match = result.generated[0]["$match"]
    assert {"age": {"$gte": 18, "$lte": 30}} in match["$and"]
    assert {"country": {"$in": ["India", "US"]}} in match["$and"]


def test_order_by_non_projected_column(compiler):
    # ORDER BY a column that is not in the SELECT list must still order by it:
    # the sort has to run before the projection drops the field.
    result = compiler().compile("SELECT name FROM users ORDER BY age")
    assert result.ok
    stages = [next(iter(stage)) for stage in result.generated]
    assert stages.index("$sort") < stages.index("$project")
    assert {"$sort": {"age": 1}} in result.generated


def test_optimization_passes_run_and_are_traced(compiler):
    result = compiler().compile("SELECT name FROM users WHERE age > 18 LIMIT 3")
    assert result.ok
    passes = {entry["pass"] for entry in result.optimization.trace}
    assert "projection_pushdown" in passes
    assert "common_subexpression_elimination" in passes
    assert "limit_pushdown" in passes


def test_disabling_optimization_keeps_original_structure(compiler):
    sql = "SELECT name FROM users WHERE age > 18 LIMIT 3"
    optimized = compiler(optimizations=True).compile(sql)
    plain = compiler(optimizations=False).compile(sql)
    assert optimized.ok and plain.ok
    # The unoptimized scan must not be narrowed by projection pushdown.
    from optisql.ir import Scan

    def first_scan(plan):
        while not isinstance(plan, Scan):
            plan = plan.inputs()[0]
        return plan

    assert first_scan(plain.optimized_plan).projection is None
    assert first_scan(optimized.optimized_plan).projection is not None


# -- Semantic validation ----------------------------------------------------


def test_unknown_field_is_semantic_error(compiler):
    result = compiler().compile("SELECT name FROM users WHERE nosuchcol = 1")
    assert not result.ok
    assert any(d.code == "E3003" for d in result.diagnostics.items)
    assert result.generated is None


def test_unknown_collection_is_semantic_error(sample_catalog):
    strict = Compiler(catalog=sample_catalog, backend="mongodb")
    result = strict.compile("SELECT name FROM unknowncoll")
    assert not result.ok
    assert any(d.code == "E3001" for d in result.diagnostics.items)


def test_type_mismatch_is_semantic_error(compiler):
    result = compiler().compile("SELECT name FROM users WHERE age = 'x'")
    assert not result.ok
    assert any(d.code == "E3007" for d in result.diagnostics.items)


def test_syntax_error_is_reported(compiler):
    result = compiler().compile("SELECT FROM WHERE")
    assert not result.ok
    assert result.diagnostics.has_errors


# -- Redis pipeline ---------------------------------------------------------


def test_redis_point_lookup_end_to_end(compiler):
    result = compiler(backend="redis").compile("SELECT name FROM users WHERE id = 42")
    assert result.ok
    assert result.generated[0]["command"] == "HMGET"


def test_redis_unsupported_shape_is_structured_error(compiler):
    result = compiler(backend="redis").compile("SELECT name FROM users WHERE age > 18")
    assert not result.ok
    assert any(d.code.startswith("E8") for d in result.diagnostics.items)


# -- CLI --------------------------------------------------------------------


def test_cli_prints_pipeline(capsys, sample_catalog, tmp_path):
    schema = {
        "collections": {
            "users": {
                "fields": {
                    "id": {"type": "integer", "indexed": True},
                    "name": {"type": "string"},
                    "age": {"type": "integer", "indexed": True},
                }
            }
        }
    }
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema))

    code = cli_main(
        [
            "--schema",
            str(schema_path),
            "--backend",
            "mongodb",
            "SELECT name FROM users WHERE age >= 18",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "MongoDB Aggregation Pipeline" in out
    assert "$match" in out


def test_cli_json_mode(capsys, tmp_path):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({"collections": {}}))
    code = cli_main(
        [
            "--schema",
            str(schema_path),
            "--json",
            "SELECT name FROM users WHERE age = 1",
        ]
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["backend"] == "mongodb"
    # The CLI uses a permissive catalog, so an unknown collection is a warning
    # and compilation still succeeds.
    assert code == 0
    assert payload["ok"] is True
    assert payload["query"][0] == {"$match": {"age": 1}}


def test_cli_no_args_returns_usage_code(capsys):
    code = cli_main([])
    assert code == 2
