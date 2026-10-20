"""Tests for the OptiQuery FastAPI REST API and React Web Server."""
import pytest
from fastapi.testclient import TestClient

from optisql.api.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_api_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "mongodb" in data["backends"]
    assert "redis" in data["backends"]


def test_api_backends(client):
    response = client.get("/api/backends")
    assert response.status_code == 200
    data = response.json()
    names = [b["name"] for b in data]
    assert "mongodb" in names
    assert "redis" in names


def test_api_schemas(client):
    response = client.get("/api/schemas")
    assert response.status_code == 200
    schemas = response.json()
    schema_ids = [s["id"] for s in schemas]
    assert "ecommerce" in schema_ids
    assert "analytics" in schema_ids

    # Fetch individual schema
    single = client.get("/api/schemas/ecommerce")
    assert single.status_code == 200
    assert single.json()["id"] == "ecommerce"

    # Non-existent schema
    missing = client.get("/api/schemas/does_not_exist")
    assert missing.status_code == 404


def test_api_presets(client):
    response = client.get("/api/presets")
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) > 0
    assert any(p["id"] == "basic_filter_project" for p in presets)


def test_api_compile_mongodb(client):
    req = {
        "sql": "SELECT name, age FROM users WHERE age >= 18 ORDER BY age DESC LIMIT 5",
        "backend": "mongodb",
        "schema_name": "ecommerce",
        "enable_optimizations": True,
    }
    response = client.post("/api/compile", json=req)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["backend"] == "mongodb"
    assert data["initial_plan"] is not None
    assert data["optimized_plan"] is not None
    assert len(data["tokens"]) > 0
    assert data["cost_comparison"] is not None
    assert data["cost_breakdown"] is not None
    assert len(data["optimization_trace"]) > 0

    # Verify MongoDB pipeline
    pipeline = data["generated_query"]
    assert isinstance(pipeline, list)
    stages = [list(stage.keys())[0] for stage in pipeline]
    assert "$match" in stages
    assert "$sort" in stages
    assert "$limit" in stages


def test_api_compile_redis(client):
    req = {
        "sql": "SELECT * FROM users WHERE id = 1",
        "backend": "redis",
        "schema_name": "ecommerce",
        "enable_optimizations": True,
    }
    response = client.post("/api/compile", json=req)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["backend"] == "redis"
    assert data["generated_query"][0]["command"] == "HGETALL"
    assert "users:1" in data["generated_query"][0]["args"][0]


def test_api_compile_no_optimize(client):
    req = {
        "sql": "SELECT name FROM users WHERE age > 21",
        "backend": "mongodb",
        "enable_optimizations": False,
    }
    response = client.post("/api/compile", json=req)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["optimization_trace"]) == 0


def test_api_compile_syntax_error(client):
    req = {
        "sql": "SELECT name, FROM users",
        "backend": "mongodb",
    }
    response = client.post("/api/compile", json=req)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert len(data["diagnostics"]) > 0
    assert any(d["severity"] == "error" for d in data["diagnostics"])


def test_api_compile_custom_schema(client):
    custom_schema = {
        "collections": {
            "products": {
                "cardinality": 500,
                "fields": {
                    "sku": {"type": "string", "indexed": True},
                    "price": {"type": "decimal"},
                },
            }
        }
    }
    req = {
        "sql": "SELECT sku, price FROM products WHERE price > 50",
        "backend": "mongodb",
        "custom_schema": custom_schema,
    }
    response = client.post("/api/compile", json=req)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["collection"] == "products"


def test_api_execute_simulation(client):
    req = {
        "sql": "SELECT name, age FROM users WHERE age >= 25 ORDER BY age DESC LIMIT 3",
        "backend": "mongodb",
        "schema_name": "ecommerce",
    }
    response = client.post("/api/execute", json=req)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["is_simulation"] is True
    assert data["row_count"] <= 3
    assert len(data["rows"]) == data["row_count"]
    if data["rows"]:
        assert "name" in data["rows"][0]
        assert "age" in data["rows"][0]


def test_serve_spa_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "OptiQuery" in response.text
