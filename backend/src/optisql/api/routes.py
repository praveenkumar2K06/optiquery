"""FastAPI API routes for OptiQuery Compiler & Optimizer Workbench."""
from __future__ import annotations

from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException

from .models import (
    BackendInfo,
    CompileRequest,
    CompileResponse,
    ExecuteRequest,
    ExecuteResponse,
    PresetQuery,
    SchemaItem,
)
from .service import PRESETS, SCHEMAS, CompilerService

router = APIRouter(prefix="/api", tags=["OptiQuery API"])
service = CompilerService()


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "OptiQuery Compiler & Optimizer",
        "version": "0.1.0",
        "backends": [b.name for b in service.get_backends_info()],
    }


@router.get("/backends", response_model=List[BackendInfo])
def get_backends() -> List[BackendInfo]:
    """Get list of supported backend adapters and their operator capabilities."""
    return service.get_backends_info()


@router.get("/schemas", response_model=List[SchemaItem])
def get_schemas() -> List[SchemaItem]:
    """Get list of pre-configured schema definitions."""
    return list(SCHEMAS.values())


@router.get("/schemas/{schema_id}", response_model=SchemaItem)
def get_schema(schema_id: str) -> SchemaItem:
    """Get details for a specific schema."""
    if schema_id not in SCHEMAS:
        raise HTTPException(status_code=404, detail=f"Schema '{schema_id}' not found")
    return SCHEMAS[schema_id]


@router.get("/presets", response_model=List[PresetQuery])
def get_presets() -> List[PresetQuery]:
    """Get preset SQL queries illustrating optimizer features and backends."""
    return PRESETS


@router.post("/compile", response_model=CompileResponse)
def compile_query(req: CompileRequest) -> CompileResponse:
    """Compile a SQL query through the full compiler & optimizer pipeline."""
    return service.compile(req)


@router.post("/execute", response_model=ExecuteResponse)
def execute_query(req: ExecuteRequest) -> ExecuteResponse:
    """Simulate or execute the query against sample or live database data."""
    return service.execute(req)

