"""Tests for backend adapters, the registry, capability validation, and the
optional execution driver.
"""
import pytest

from optisql.backends import (
    Backend,
    BackendCapability,
    BackendOperation,
    MongoDBBackend,
    RedisBackend,
    available_backends,
    get_backend,
    has_backend,
)
from optisql.backends.base import NODE_CAPABILITY
from optisql.codegen.redis import RedisDataModel
from optisql.compiler import Compiler
from optisql.execution import ExecutionResult, QueryExecutor
from optisql.ir import (
    BinaryExpr,
    ColumnRef,
    Filter,
    Join,
    Limit,
    LiteralValue,
    Project,
    Scan,
    Sort,
)
from optisql.metadata import Catalog, FieldType


def _lit(v, kind="string"):
    return LiteralValue(value=v, kind=kind, type=FieldType.STRING)


def _mongo_plan():
    return Limit(
        count=5,
        input=Sort(
            keys=[(ColumnRef("age"), False)],
            input=Project(
                items=[("name", ColumnRef("name")), ("age", ColumnRef("age"))],
                input=Filter(
                    predicate=BinaryExpr(
                        op="AND",
                        left=BinaryExpr(
                            op="=", left=ColumnRef("country"), right=_lit("India")
                        ),
                        right=BinaryExpr(
                            op=">=",
                            left=ColumnRef("age"),
                            right=LiteralValue(value=18, kind="integer"),
                        ),
                    ),
                    input=Scan(collection="users"),
                ),
            ),
        ),
    )


# -- Registry ---------------------------------------------------------------


def test_available_backends_lists_builtins():
    names = available_backends()
    assert "mongodb" in names
    assert "redis" in names
    assert names == sorted(names)


def test_get_backend_returns_adapter():
    backend = get_backend("mongodb")
    assert isinstance(backend, MongoDBBackend)
    assert backend.name == "mongodb"
    assert isinstance(get_backend("redis"), RedisBackend)


def test_get_backend_is_case_insensitive():
    assert get_backend("MongoDB").name == "mongodb"
    assert has_backend("REDIS")


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError):
        get_backend("cassandra")


# -- Capabilities / validation ---------------------------------------------


def test_mongodb_capabilities():
    backend = MongoDBBackend()
    for cap in (
        BackendCapability.SCAN,
        BackendCapability.FILTER,
        BackendCapability.PROJECT,
        BackendCapability.SORT,
        BackendCapability.LIMIT,
        BackendCapability.GROUP_BY,
        BackendCapability.AGGREGATES,
    ):
        assert backend.supports(cap)


def test_required_capabilities_walks_plan():
    backend = MongoDBBackend()
    required = backend.required_capabilities(_mongo_plan())
    assert BackendCapability.SCAN in required
    assert BackendCapability.FILTER in required
    assert BackendCapability.SORT in required
    assert BackendCapability.LIMIT in required
    assert BackendCapability.PROJECT in required


def test_validate_flags_unsupported_join_for_redis():
    backend = RedisBackend()
    plan = Join(
        left=Scan(collection="a"), right=Scan(collection="b"), condition=None
    )
    codes = [d.code for d in backend.validate(plan).items]
    assert "E9101" in codes


def test_validate_clean_for_supported_plan():
    backend = MongoDBBackend()
    diags = backend.validate(_mongo_plan())
    assert not diags.has_errors


def test_node_capability_covers_all_plan_ops():
    for op in (Scan, Filter, Project, Sort, Limit, Join):
        assert op in NODE_CAPABILITY


# -- Generation -------------------------------------------------------------


def test_mongodb_generate_returns_operation():
    backend = MongoDBBackend()
    op = backend.generate(_mongo_plan())
    assert isinstance(op, BackendOperation)
    assert op.backend == "mongodb"
    assert op.supported
    assert op.ok
    assert op.collection == "users"
    assert isinstance(op.query, list)
    assert any("$match" in stage for stage in op.query)


def test_mongodb_generate_unsupported_operator():
    backend = MongoDBBackend()
    op = backend.generate(Join(left=Scan(collection="a"), right=Scan(collection="b")))
    assert not op.supported
    assert op.diagnostics.has_errors


def test_redis_generate_point_lookup():
    backend = RedisBackend()
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("id"), right=_lit("42")),
        input=Scan(collection="users"),
    )
    op = backend.generate(plan)
    assert op.supported
    assert op.query[0]["command"] == "HGETALL"


def test_redis_generate_rejects_range():
    backend = RedisBackend()
    plan = Filter(
        predicate=BinaryExpr(
            op=">", left=ColumnRef("age"), right=LiteralValue(value=18, kind="integer")
        ),
        input=Scan(collection="users"),
    )
    op = backend.generate(plan)
    assert not op.supported
    assert any(d.code == "E8003" for d in op.diagnostics.items)


def test_redis_backend_uses_data_model():
    model = RedisDataModel(key_prefix="app", primary_key="id")
    backend = RedisBackend(data_model=model)
    plan = Filter(
        predicate=BinaryExpr(op="=", left=ColumnRef("id"), right=_lit("7")),
        input=Scan(collection="users"),
    )
    op = backend.generate(plan)
    assert op.query[0]["args"] == ["app:users:7"]


def test_backend_operation_to_json():
    backend = MongoDBBackend()
    op = backend.generate(_mongo_plan())
    assert "$match" in op.to_json()


# -- Execution availability -------------------------------------------------


def test_can_execute_false_without_driver():
    # No pymongo/redis drivers are installed in the test environment, so no
    # backend should claim it can execute.
    assert not MongoDBBackend().can_execute(_mongo_plan())
    assert not RedisBackend().can_execute(Scan(collection="users"))
    assert not MongoDBBackend().is_available()


def test_describe_reports_execution_availability():
    info = MongoDBBackend().describe()
    assert info["name"] == "mongodb"
    assert info["execution_available"] is False
    assert "scan" in info["capabilities"]


# -- Executor ---------------------------------------------------------------


def test_executor_disabled_is_dry_run():
    executor = QueryExecutor(backend=MongoDBBackend(), enabled=False)
    result = executor.execute(_mongo_plan())
    assert isinstance(result, ExecutionResult)
    assert result.success
    assert result.executed is False
    assert result.backend == "mongodb"


def test_executor_enabled_without_connection_reports_error():
    executor = QueryExecutor(backend=MongoDBBackend(), enabled=True)
    result = executor.execute(_mongo_plan())
    assert result.executed is False
    assert result.success is False
    assert result.error is not None


def test_executor_enabled_without_backend_reports_error():
    executor = QueryExecutor(backend=None, enabled=True)
    result = executor.execute(_mongo_plan())
    assert result.success is False
    assert result.error is not None


def test_executor_uses_injected_backend():
    class FakeBackend(Backend):
        name = "fake"

        def __init__(self):
            super().__init__()
            self._capabilities = {BackendCapability.SCAN, BackendCapability.EXECUTION}

        def is_available(self):
            return True

        def generate(self, plan):
            return BackendOperation(
                backend=self.name, supported=True, query=[{"command": "ping"}]
            )

        def execute(self, plan, **kwargs):
            return [{"ok": 1}]

    executor = QueryExecutor(backend=FakeBackend(), enabled=True)
    result = executor.execute(Scan(collection="users"))
    assert result.executed
    assert result.success
    assert result.rows == [{"ok": 1}]
    assert result.row_count == 1
    assert result.elapsed_ms is not None


# -- Compiler integration ---------------------------------------------------


def test_compiler_populates_backend_operation():
    catalog = Catalog(strict=False)
    compiler = Compiler(catalog=catalog, backend="mongodb")
    result = compiler.compile(
        "SELECT name FROM users WHERE age >= 18 ORDER BY name LIMIT 3"
    )
    assert result.ok
    assert result.backend_operation is not None
    assert result.backend_operation.backend == "mongodb"
    assert result.generated == result.backend_operation.query


def test_compiler_unknown_backend_reports_error():
    compiler = Compiler(catalog=Catalog(strict=False), backend="nope")
    result = compiler.compile("SELECT name FROM users")
    assert not result.ok
    assert any(d.code == "E9001" for d in result.diagnostics.items)


def test_compiler_uses_provided_adapter():
    model = RedisDataModel(key_prefix="app")
    adapter = RedisBackend(data_model=model)
    compiler = Compiler(
        catalog=Catalog(strict=False), backend="redis", backend_adapter=adapter
    )
    result = compiler.compile("SELECT name FROM users WHERE id = '5'")
    assert result.ok
    assert result.backend == "redis"
    assert result.generated[0]["args"][0] == "app:users:5"
