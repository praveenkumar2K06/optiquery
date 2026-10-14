"""Top-level compilation driver for OptiQuery.

Runs the full pipeline:

    SQL text -> Lexer -> Parser/AST -> Semantic Analysis -> IR (lowering)
             -> Optimization (rule passes + cost estimate) -> Backend codegen

The driver is backend-agnostic: it produces an :class:`IR` plan and a
:class:`CompilationResult`, and dispatches code generation to the selected
backend. It never touches a live database unless execution is explicitly
enabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .ast import Statement
from .backends import Backend, BackendOperation, get_backend
from .diagnostics import Diagnostic, DiagnosticBag
from .ir import LogicalPlan, plan_to_string
from .metadata import Catalog
from .optimizer import OptimizationResult, QueryOptimizer
from .parser import Parser
from .planner import lower
from .semantic import SemanticResult, analyze


@dataclass
class CompilationResult:
    """The complete result of compiling one SQL statement."""

    source: str
    backend: str = "mongodb"
    statement: Optional[Statement] = None
    ast: Optional[Statement] = None
    semantic: Optional[SemanticResult] = None
    logical_plan: Optional[LogicalPlan] = None
    optimization: Optional[OptimizationResult] = None
    generated: object = None
    backend_operation: Optional[BackendOperation] = None
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    ast_debug: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.diagnostics.has_errors

    @property
    def optimized_plan(self) -> Optional[LogicalPlan]:
        if self.optimization is None:
            return self.logical_plan
        return self.optimization.optimized

    def plan_explanation(self) -> str:
        if self.optimized_plan is None:
            return "<no plan>"
        return plan_to_string(self.optimized_plan)

    def all_diagnostics(self) -> List[Diagnostic]:
        return self.diagnostics.items


class Compiler:
    """Compiles SQL into backend queries through the full pipeline."""

    def __init__(
        self,
        catalog: Optional[Catalog] = None,
        backend: str = "mongodb",
        enable_optimizations: bool = True,
        backend_adapter: Optional[Backend] = None,
    ):
        self.catalog = catalog if catalog is not None else Catalog()
        self.backend = backend if backend_adapter is None else backend_adapter.name
        self.enable_optimizations = enable_optimizations
        self.backend_adapter = backend_adapter

    def _resolve_backend(self) -> Optional[Backend]:
        if self.backend_adapter is not None:
            return self.backend_adapter
        try:
            self.backend_adapter = get_backend(self.backend)
        except ValueError:
            return None
        return self.backend_adapter

    def compile(self, source: str) -> CompilationResult:
        result = CompilationResult(source=source, backend=self.backend)
        diagnostics = DiagnosticBag()
        result.diagnostics = diagnostics

        # Phase 1+2: Lex and parse. The parser lexes the full input up front,
        # so both lexical and syntax diagnostics are captured in one bag.
        parser = Parser(source, diagnostics)
        statement = parser.parse()
        if statement is None:
            return result
        result.statement = statement
        result.ast = statement

        # Phase 3: Semantic analysis.
        sem = analyze(statement, self.catalog)
        result.semantic = sem
        diagnostics.extend(sem.diagnostics.items)
        if sem.diagnostics.has_errors:
            return result

        # Phase 5: Lower to IR.
        plan = lower(statement)
        result.logical_plan = plan

        # Phase 6+7: Optimize and estimate cost.
        optimizer = QueryOptimizer(
            catalog=self.catalog, enable_passes=self.enable_optimizations
        )
        opt = optimizer.optimize(plan)
        result.optimization = opt

        # Phase 8/9: Backend code generation.
        backend = self._resolve_backend()
        if backend is None:
            diagnostics.error("E9001", f"Unknown backend '{self.backend}'", None)
            return result

        operation = backend.generate(opt.optimized)
        result.backend_operation = operation
        result.generated = operation.query
        diagnostics.extend(operation.diagnostics.items)

        return result

    def compile_to_plan(self, source: str) -> CompilationResult:
        return self.compile(source)
