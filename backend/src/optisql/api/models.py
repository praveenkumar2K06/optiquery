"""Pydantic data models for the OptiQuery FastAPI backend."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TokenModel(BaseModel):
    type: str
    value: Any
    lexeme: str
    line: int
    column: int
    length: int


class DiagnosticModel(BaseModel):
    code: str
    severity: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    offset: Optional[int] = None
    length: Optional[int] = None


class CostComponent(BaseModel):
    name: str
    value: float
    notes: str = ""


class CostBreakdown(BaseModel):
    total: float
    components: Dict[str, CostComponent] = Field(default_factory=dict)
    approximate: bool = True
    notes: str = ""


class CostComparison(BaseModel):
    original_cost: float
    optimized_cost: float
    savings_pct: float
    is_lower: bool


class OptimizationTraceEntry(BaseModel):
    pass_name: str
    changes: int
    details: Dict[str, Any] = Field(default_factory=dict)


class CandidatePlanModel(BaseModel):
    rule: str
    cost_total: float
    rationale: str


class CompileRequest(BaseModel):
    sql: str = Field(..., description="SQL query string to compile")
    backend: str = Field(default="mongodb", description="Target backend: mongodb or redis")
    schema_name: Optional[str] = Field(default="ecommerce", description="Name of preset schema to use")
    custom_schema: Optional[Dict[str, Any]] = Field(default=None, description="Custom JSON schema if any")
    enable_optimizations: bool = Field(default=True, description="Whether to run optimization passes")
    strict_catalog: bool = Field(default=False, description="Whether unknown tables/fields raise errors")


class CompileResponse(BaseModel):
    ok: bool
    sql: str
    backend: str
    ast: Optional[Dict[str, Any]] = None
    ast_repr: Optional[str] = None
    tokens: List[TokenModel] = Field(default_factory=list)
    initial_plan: Optional[Dict[str, Any]] = None
    initial_plan_text: Optional[str] = None
    optimized_plan: Optional[Dict[str, Any]] = None
    optimized_plan_text: Optional[str] = None
    optimization_trace: List[OptimizationTraceEntry] = Field(default_factory=list)
    candidates: List[CandidatePlanModel] = Field(default_factory=list)
    cost_breakdown: Optional[CostBreakdown] = None
    cost_comparison: Optional[CostComparison] = None
    generated_query: Any = None
    query_formatted: Optional[str] = None
    collection: str = ""
    notes: List[str] = Field(default_factory=list)
    diagnostics: List[DiagnosticModel] = Field(default_factory=list)
    selection_rationale: Optional[str] = None


class ExecuteRequest(BaseModel):
    sql: str
    backend: str = "mongodb"
    schema_name: Optional[str] = "ecommerce"
    custom_schema: Optional[Dict[str, Any]] = None
    enable_optimizations: bool = True


class ExecuteResponse(BaseModel):
    success: bool
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    backend: str = "mongodb"
    is_simulation: bool = True
    columns: List[str] = Field(default_factory=list)


class SchemaItem(BaseModel):
    id: str
    name: str
    description: str
    schema_dict: Dict[str, Any]


class PresetQuery(BaseModel):
    id: str
    title: str
    category: str
    description: str
    sql: str
    backend: str = "mongodb"
    schema_name: str = "ecommerce"


class BackendInfo(BaseModel):
    name: str
    title: str
    description: str
    capabilities: List[str]
    output_format: str

