"""Semantic analyzer for the OptiQuery SQL subset.

Responsibilities:

* resolve collection and field references against the schema catalog,
* build a symbol table,
* infer and check expression types,
* enforce aggregate / GROUP BY / HAVING consistency,
* validate ORDER BY, LIMIT, and OFFSET,
* report unsupported constructs explicitly.

When a collection is not present in the catalog and the catalog is configured
permissively, the analyzer emits a warning and treats the collection's fields as
unknown (``ANY``) rather than silently claiming validation succeeded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..ast import (
    BetweenExpr,
    BinaryOp,
    CaseExpr,
    CreateTableStatement,
    DeleteStatement,
    Expr,
    FunctionCall,
    Identifier,
    InListExpr,
    InsertStatement,
    IsNullExpr,
    LikeExpr,
    Literal,
    SelectItem,
    SelectStatement,
    Star,
    Statement,
    UnaryOp,
    UpdateStatement,
)
from ..diagnostics import DiagnosticBag, SourceSpan
from ..metadata import (
    AGGREGATE_RESULT_TYPE,
    AGGREGATE_FUNCTIONS,
    SCALAR_FUNCTIONS,
    Catalog,
    CollectionSchema,
    FieldType,
)
from ..symbols import Symbol, SymbolTable

_COMPARISON_OPS = {"=", "!=", "<", ">", "<=", ">="}
_LOGICAL_OPS = {"AND", "OR"}
_ARITHMETIC_OPS = {"+", "-", "*", "/", "%"}


@dataclass
class SemanticResult:
    """Output of semantic analysis."""

    statement: Statement
    symbols: SymbolTable
    diagnostics: DiagnosticBag
    #: True when every referenced collection/field was validated against known
    #: schema metadata (as opposed to inferred permissively).
    fully_validated: bool = True

    @property
    def ok(self) -> bool:
        return not self.diagnostics.has_errors


class SemanticAnalyzer:
    """Performs name resolution and semantic checks on a statement."""

    def __init__(self, catalog: Optional[Catalog] = None):
        self.catalog = catalog if catalog is not None else Catalog()
        self.diagnostics = DiagnosticBag()
        self.symbols = SymbolTable()
        self._permissive_aliases: Set[str] = set()
        self._fully_validated = True

    # -- Public API --------------------------------------------------------

    def analyze(self, statement: Statement) -> SemanticResult:
        if isinstance(statement, SelectStatement):
            self._analyze_select(statement)
        elif isinstance(statement, InsertStatement):
            self._analyze_insert(statement)
        elif isinstance(statement, UpdateStatement):
            self._analyze_update(statement)
        elif isinstance(statement, DeleteStatement):
            self._analyze_delete(statement)
        elif isinstance(statement, CreateTableStatement):
            self._analyze_create_table(statement)
        else:
            self.diagnostics.error(
                "E3000",
                f"Unsupported statement type {type(statement).__name__}",
                statement.span,
            )
        return SemanticResult(
            statement=statement,
            symbols=self.symbols,
            diagnostics=self.diagnostics,
            fully_validated=self._fully_validated,
        )

    # -- Collection / alias binding ---------------------------------------

    def _bind_table(self, name: str, alias: Optional[str], span) -> str:
        """Bind a table reference, returning the effective alias."""
        effective = alias or name
        key = effective.lower()

        schema = self.catalog.get_collection(name)
        if schema is None:
            if self.catalog.strict:
                self.diagnostics.error(
                    "E3001", f"Unknown collection '{name}'", span
                )
                self._fully_validated = False
                schema = CollectionSchema(name=name)
            else:
                self.diagnostics.warning(
                    "W3001",
                    f"Collection '{name}' is not in the schema catalog; "
                    f"fields are treated as unresolved (ANY)",
                    span,
                )
                schema = CollectionSchema(name=name)
                self._permissive_aliases.add(key)
                self._fully_validated = False

        if self.symbols.has_alias(effective):
            self.diagnostics.error(
                "E3002",
                f"Duplicate or conflicting table alias '{effective}'",
                span,
            )
            return effective

        fields = list(getattr(schema, "fields", {}).keys())
        self.symbols.bind_collection(effective, schema, fields)
        return effective

    def _is_permissive(self, alias: str) -> bool:
        return alias.lower() in self._permissive_aliases

    # -- SELECT ------------------------------------------------------------

    def _analyze_select(self, stmt: SelectStatement) -> None:
        if stmt.from_table is not None:
            self._bind_table(
                stmt.from_table.name, stmt.from_table.alias, stmt.from_table.span
            )
        for join in stmt.joins:
            self._bind_table(join.table.name, join.table.alias, join.table.span)
            if join.condition is not None:
                self._check_expr(join.condition, allow_aggregate=False)

        is_aggregate_query = bool(stmt.group_by) or stmt.has_aggregates

        # Resolve the projection list.
        group_by_keys = {_expr_key(e) for e in stmt.group_by}
        for item in stmt.items:
            if isinstance(item.expr, Star):
                self._check_star(item.expr, stmt)
            else:
                self._check_expr(item.expr, allow_aggregate=True)
                if is_aggregate_query and not _contains_aggregate(item.expr):
                    key = _expr_key(item.expr)
                    if key and key not in group_by_keys:
                        self.diagnostics.error(
                            "E3008",
                            "Non-aggregated column must appear in GROUP BY "
                            "or be used in an aggregate function",
                            item.expr.span,
                        )
            if item.alias is not None:
                self._define_select_alias(item)

        # GROUP BY expressions must resolve.
        for gexpr in stmt.group_by:
            self._check_expr(gexpr, allow_aggregate=False)

        # WHERE cannot contain aggregates.
        if stmt.where is not None:
            self._check_expr(stmt.where, allow_aggregate=False)
            if _contains_aggregate(stmt.where):
                self.diagnostics.error(
                    "E3008", "Aggregate functions are not allowed in WHERE", stmt.where.span
                )

        # HAVING may reference aggregates.
        if stmt.having is not None:
            self._check_expr(stmt.having, allow_aggregate=True)
            if not is_aggregate_query:
                self.diagnostics.error(
                    "E3011",
                    "HAVING requires GROUP BY or an aggregate in the query",
                    stmt.having.span,
                )

        # ORDER BY: select alias, aggregate, or valid field reference.
        for order in stmt.order_by:
            self._check_order_by(order.expr, is_aggregate_query)

        # LIMIT / OFFSET must be non-negative integer constants.
        for label, value in (("LIMIT", stmt.limit), ("OFFSET", stmt.offset)):
            if value is not None:
                self._check_limit(value, label)

        # Unsupported combinations.
        if stmt.joins and not self.catalog.strict:
            pass  # allowed, but backends may reject
        if isinstance(stmt.items[0].expr, Star) and stmt.distinct is False:
            pass
        if any(isinstance(i.expr, Star) for i in stmt.items) and (
            is_aggregate_query or len(stmt.joins) > 0
        ):
            self.diagnostics.error(
                "E3005",
                "SELECT * is not supported together with GROUP BY, "
                "aggregates, or JOINs; list the columns explicitly",
                stmt.items[0].span,
            )

        # DISTINCT with aggregate is unsupported in the subset.
        if stmt.distinct and is_aggregate_query:
            self.diagnostics.warning(
                "W3003",
                "SELECT DISTINCT with aggregates may not behave as expected",
                stmt.span,
            )

    def _define_select_alias(self, item: SelectItem) -> None:
        alias = item.alias
        if alias.lower() in self.symbols.select_aliases:
            self.diagnostics.error(
                "E3002", f"Duplicate column alias '{alias}'", item.span
            )
        rtype = _as_field_type(item.expr.resolved_type)
        self.symbols.define_alias_symbol(
            Symbol(name=alias, kind="alias", type=rtype, is_alias=True)
        )

    def _check_star(self, star: Star, stmt: SelectStatement) -> None:
        if star.qualifier is not None:
            if not self.symbols.has_alias(star.qualifier):
                self.diagnostics.error(
                    "E3003",
                    f"Unknown table alias '{star.qualifier}' in '{star.qualifier}.*'",
                    star.span,
                )
        elif self.symbols.alias_count() == 0 and stmt.from_table is None:
            self.diagnostics.error(
                "E3005", "SELECT * requires a FROM clause", star.span
            )

    def _check_order_by(self, expr: Expr, is_aggregate_query: bool) -> None:
        # A bare identifier may be a select alias.
        if isinstance(expr, Identifier) and len(expr.parts) == 1:
            existing = self.symbols.resolve_select_alias(expr.parts[0])
            if existing is not None:
                expr.resolved_type = existing.type
                expr.resolved_field = existing.name
                return
            # Or a positional reference (1-based integer literal handled below).
        if isinstance(expr, Literal) and expr.kind == "integer":
            return
        self._check_expr(expr, allow_aggregate=is_aggregate_query)
        if not is_aggregate_query and _contains_aggregate(expr):
            self.diagnostics.error(
                "E3009",
                "Aggregate in ORDER BY requires GROUP BY or an aggregate query",
                expr.span,
            )

    def _check_limit(self, value: Expr, label: str) -> None:
        if not isinstance(value, Literal) or value.kind != "integer":
            self.diagnostics.error(
                "E3010",
                f"{label} must be a non-negative integer literal",
                value.span,
            )
            return
        if isinstance(value.value, int) and value.value < 0:
            self.diagnostics.error(
                "E3010", f"{label} must not be negative", value.span
            )
        value.resolved_type = FieldType.INTEGER

    # -- INSERT / UPDATE / DELETE / CREATE --------------------------------

    def _analyze_insert(self, stmt: InsertStatement) -> None:
        alias = self._bind_table(stmt.table.name, None, stmt.table.span)
        schema = self.symbols.resolve_alias(alias)
        columns = stmt.columns
        if columns:
            for col in columns:
                self._check_field_exists(alias, col, stmt.table.span)
            for row in stmt.rows:
                if len(row) != len(columns):
                    self.diagnostics.error(
                        "E3006",
                        f"INSERT row has {len(row)} values but {len(columns)} "
                        f"columns were listed",
                        stmt.span,
                    )
                    break
        else:
            # Infer column order from schema when available.
            inferred = list(schema.fields.keys()) if schema else []
            if inferred:
                for row in stmt.rows:
                    if len(row) != len(inferred):
                        self.diagnostics.warning(
                            "W3004",
                            f"INSERT without column list has {len(row)} values "
                            f"but schema has {len(inferred)} fields",
                            stmt.span,
                        )
                        break
        for row in stmt.rows:
            for value in row:
                self._check_expr(value, allow_aggregate=False)

    def _analyze_update(self, stmt: UpdateStatement) -> None:
        alias = self._bind_table(stmt.table.name, None, stmt.table.span)
        for assignment in stmt.assignments:
            self._check_field_exists(alias, assignment.field, assignment.span)
            self._check_expr(assignment.value, allow_aggregate=False)
        if stmt.where is not None:
            self._check_expr(stmt.where, allow_aggregate=False)

    def _analyze_delete(self, stmt: DeleteStatement) -> None:
        self._bind_table(stmt.table.name, None, stmt.table.span)
        if stmt.where is not None:
            self._check_expr(stmt.where, allow_aggregate=False)

    def _analyze_create_table(self, stmt: CreateTableStatement) -> None:
        seen: Set[str] = set()
        for col in stmt.columns:
            if col.name.lower() in seen:
                self.diagnostics.error(
                    "E3002", f"Duplicate column '{col.name}' in CREATE TABLE", col.span
                )
            seen.add(col.name.lower())
            if _parse_type_name(col.type_name) is None:
                self.diagnostics.error(
                    "E3007",
                    f"Unknown column type '{col.type_name}'",
                    col.span,
                )

    # -- Expression checking ----------------------------------------------

    def _check_expr(self, expr: Optional[Expr], allow_aggregate: bool) -> Optional[FieldType]:
        """Resolve an expression, recording diagnostics and returning its type."""
        if expr is None:
            return None

        if isinstance(expr, Literal):
            expr.resolved_type = _literal_type(expr)
            return _as_field_type(expr.resolved_type)

        if isinstance(expr, Star):
            return FieldType.ANY

        if isinstance(expr, Identifier):
            return self._check_identifier(expr)

        if isinstance(expr, UnaryOp):
            operand_type = self._check_expr(expr.operand, allow_aggregate)
            if expr.op == "NOT":
                expr.resolved_type = FieldType.BOOLEAN
                return FieldType.BOOLEAN
            if expr.op == "-":
                if operand_type is not None and not operand_type.is_numeric and operand_type is not FieldType.ANY:
                    self.diagnostics.error(
                        "E3007",
                        f"Unary '-' requires a numeric operand, got {operand_type.value}",
                        expr.span,
                    )
                expr.resolved_type = operand_type or FieldType.DECIMAL
                return _as_field_type(expr.resolved_type)
            expr.resolved_type = FieldType.ANY
            return FieldType.ANY

        if isinstance(expr, BinaryOp):
            return self._check_binary(expr, allow_aggregate)

        if isinstance(expr, FunctionCall):
            return self._check_function(expr, allow_aggregate)

        if isinstance(expr, BetweenExpr):
            self._check_expr(expr.operand, allow_aggregate)
            self._check_expr(expr.low, allow_aggregate)
            self._check_expr(expr.high, allow_aggregate)
            self._check_comparable(expr.operand, expr.low)
            self._check_comparable(expr.operand, expr.high)
            expr.resolved_type = FieldType.BOOLEAN
            return FieldType.BOOLEAN

        if isinstance(expr, InListExpr):
            self._check_expr(expr.operand, allow_aggregate)
            for option in expr.options:
                self._check_expr(option, allow_aggregate)
                self._check_comparable(expr.operand, option)
            expr.resolved_type = FieldType.BOOLEAN
            return FieldType.BOOLEAN

        if isinstance(expr, LikeExpr):
            self._check_expr(expr.operand, allow_aggregate)
            self._check_expr(expr.pattern, allow_aggregate)
            expr.resolved_type = FieldType.BOOLEAN
            return FieldType.BOOLEAN

        if isinstance(expr, IsNullExpr):
            self._check_expr(expr.operand, allow_aggregate)
            expr.resolved_type = FieldType.BOOLEAN
            return FieldType.BOOLEAN

        if isinstance(expr, CaseExpr):
            return self._check_case(expr, allow_aggregate)

        return FieldType.ANY

    def _check_binary(self, expr: BinaryOp, allow_aggregate: bool) -> FieldType:
        left_type = self._check_expr(expr.left, allow_aggregate)
        right_type = self._check_expr(expr.right, allow_aggregate)

        if expr.op in _COMPARISON_OPS:
            self._check_comparable(expr.left, expr.right)
            expr.resolved_type = FieldType.BOOLEAN
            return FieldType.BOOLEAN

        if expr.op in _LOGICAL_OPS:
            for side, t in ((expr.left, left_type), (expr.right, right_type)):
                if t is not None and t not in (FieldType.BOOLEAN, FieldType.ANY):
                    self.diagnostics.error(
                        "E3007",
                        f"Logical operator '{expr.op}' requires boolean operands, "
                        f"got {t.value}",
                        side.span,
                    )
            expr.resolved_type = FieldType.BOOLEAN
            return FieldType.BOOLEAN

        if expr.op in _ARITHMETIC_OPS:
            if expr.op == "/" and isinstance(expr.right, Literal) and expr.right.value in (0, 0.0):
                self.diagnostics.warning(
                    "W3005", "Division by zero in constant expression", expr.span
                )
            result = _numeric_result(left_type, right_type)
            for side, t in ((expr.left, left_type), (expr.right, right_type)):
                if t is not None and t not in (FieldType.ANY, FieldType.NULL) and not t.is_numeric:
                    if expr.op == "+":
                        # String concatenation is not supported explicitly.
                        self.diagnostics.error(
                            "E3007",
                            f"Arithmetic operator '{expr.op}' requires numeric "
                            f"operands, got {t.value}",
                            side.span,
                        )
            expr.resolved_type = result
            return result

        expr.resolved_type = FieldType.ANY
        return FieldType.ANY

    def _check_function(self, expr: FunctionCall, allow_aggregate: bool) -> FieldType:
        name = expr.name.upper()

        if name in AGGREGATE_FUNCTIONS:
            if not allow_aggregate:
                self.diagnostics.error(
                    "E3008",
                    f"Aggregate function {name} is not allowed in this context",
                    expr.span,
                )
            if name != "COUNT" and not expr.args:
                self.diagnostics.error(
                    "E3006", f"{name} requires one argument", expr.span
                )
            if name == "COUNT" and len(expr.args) > 1:
                self.diagnostics.error(
                    "E3006", "COUNT accepts at most one argument", expr.span
                )
            for arg in expr.args:
                if not isinstance(arg, Star):
                    arg_type = self._check_expr(arg, allow_aggregate=False)
                    if _contains_aggregate(arg):
                        self.diagnostics.error(
                            "E3008",
                            "Nested aggregate functions are not allowed",
                            arg.span,
                        )
                    if name in ("SUM", "AVG") and arg_type is not None and not arg_type.is_numeric and arg_type is not FieldType.ANY:
                        self.diagnostics.error(
                            "E3006",
                            f"{name} requires a numeric argument, got {arg_type.value}",
                            arg.span,
                        )
            result = AGGREGATE_RESULT_TYPE[name]
            if result is FieldType.ANY and expr.args and not isinstance(expr.args[0], Star):
                inferred = _as_field_type(expr.args[0].resolved_type)
                if inferred is not FieldType.ANY:
                    result = inferred
            expr.resolved_type = result
            return result

        if name in SCALAR_FUNCTIONS:
            for arg in expr.args:
                self._check_expr(arg, allow_aggregate)
            if name in ("LOWER", "UPPER", "LENGTH") and expr.args:
                expr.resolved_type = FieldType.STRING
                return FieldType.STRING
            if name in ("ABS", "ROUND") and expr.args:
                expr.resolved_type = _as_field_type(expr.args[0].resolved_type) or FieldType.DECIMAL
                return _as_field_type(expr.resolved_type)
            expr.resolved_type = FieldType.ANY
            return FieldType.ANY

        self.diagnostics.error("E3006", f"Unknown function '{expr.name}'", expr.span)
        for arg in expr.args:
            if not isinstance(arg, Star):
                self._check_expr(arg, allow_aggregate)
        expr.resolved_type = FieldType.ANY
        return FieldType.ANY

    def _check_case(self, expr: CaseExpr, allow_aggregate: bool) -> FieldType:
        if expr.operand is not None:
            self._check_expr(expr.operand, allow_aggregate)
        result_type: Optional[FieldType] = None
        for when in expr.whens:
            self._check_expr(when.condition, allow_aggregate)
            self._check_expr(when.result, allow_aggregate)
            rtype = _as_field_type(when.result.resolved_type)
            result_type = _unify_types(result_type, rtype)
        if expr.default is not None:
            self._check_expr(expr.default, allow_aggregate)
            result_type = _unify_types(result_type, _as_field_type(expr.default.resolved_type))
        if not expr.whens:
            self.diagnostics.error(
                "E3012", "CASE expression requires at least one WHEN branch", expr.span
            )
        expr.resolved_type = result_type or FieldType.ANY
        return _as_field_type(expr.resolved_type)

    # -- Identifier resolution --------------------------------------------

    def _check_identifier(self, expr: Identifier) -> FieldType:
        if not expr.parts:
            return FieldType.ANY

        if len(expr.parts) == 1:
            return self._resolve_unqualified(expr)

        # Qualified: qualifier.field
        qualifier = ".".join(expr.parts[:-1])
        field = expr.parts[-1]
        if not self.symbols.has_alias(qualifier):
            self.diagnostics.error(
                "E3003", f"Unknown table alias '{qualifier}'", expr.span
            )
            expr.resolved_type = FieldType.ANY
            return FieldType.ANY
        return self._resolve_field(qualifier, field, expr)

    def _resolve_unqualified(self, expr: Identifier) -> FieldType:
        field = expr.parts[0]

        # A SELECT alias may be referenced (mainly in ORDER BY/HAVING).
        alias_symbol = self.symbols.resolve_select_alias(field)
        if alias_symbol is not None:
            expr.resolved_type = alias_symbol.type
            expr.resolved_field = alias_symbol.name
            return alias_symbol.type

        # Search every bound collection for the field.
        matches: List[str] = []
        for alias in self.symbols.aliases:
            schema = self.symbols.resolve_alias(alias)
            if schema is None:
                continue
            if self._is_permissive(alias):
                matches.append(alias)
                continue
            if schema.has_field(field):
                matches.append(alias)

        if len(matches) == 1:
            return self._resolve_field(matches[0], field, expr)
        if len(matches) > 1:
            self.diagnostics.error(
                "E3004",
                f"Ambiguous field reference '{field}' could refer to "
                f"{', '.join(sorted(matches))}",
                expr.span,
            )
            expr.resolved_type = FieldType.ANY
            return FieldType.ANY

        # Not found anywhere.
        if self.symbols.alias_count() == 0:
            self.diagnostics.error(
                "E3003", f"Unknown field '{field}' (no FROM clause)", expr.span
            )
        elif all(self._is_permissive(a) for a in self.symbols.aliases):
            self.diagnostics.warning(
                "W3002",
                f"Field '{field}' could not be validated (unknown schema)",
                expr.span,
            )
            self._fully_validated = False
            expr.resolved_field = field
            expr.resolved_type = FieldType.ANY
            return FieldType.ANY
        else:
            self.diagnostics.error("E3003", f"Unknown field '{field}'", expr.span)
        expr.resolved_type = FieldType.ANY
        return FieldType.ANY

    def _resolve_field(self, alias: str, field: str, expr: Identifier) -> FieldType:
        schema = self.symbols.resolve_alias(alias)
        if self._is_permissive(alias):
            expr.resolved_field = field
            expr.resolved_type = FieldType.ANY
            self._fully_validated = False
            return FieldType.ANY

        assert schema is not None
        field_def = schema.get_field(field)
        if field_def is None:
            self.diagnostics.error(
                "E3003", f"Unknown field '{field}' in collection '{alias}'", expr.span
            )
            expr.resolved_type = FieldType.ANY
            return FieldType.ANY
        expr.resolved_field = field_def.name
        expr.resolved_type = field_def.type
        return field_def.type

    def _check_field_exists(self, alias: str, field: str, span) -> None:
        if self._is_permissive(alias):
            self._fully_validated = False
            return
        schema = self.symbols.resolve_alias(alias)
        if schema is not None and not schema.has_field(field):
            self.diagnostics.error(
                "E3003", f"Unknown field '{field}' in collection '{alias}'", span
            )

    # -- Type compatibility -----------------------------------------------

    def _check_comparable(self, left: Expr, right: Expr) -> None:
        lt = _as_field_type(left.resolved_type)
        rt = _as_field_type(right.resolved_type)
        if lt is None or rt is None:
            return
        if FieldType.ANY in (lt, rt) or FieldType.NULL in (lt, rt):
            return
        # Numeric cross-comparison is allowed.
        if lt.is_numeric and rt.is_numeric:
            return
        if lt != rt:
            self.diagnostics.error(
                "E3007",
                f"Incompatible types in comparison: {lt.value} vs {rt.value}",
                _merge_span(left, right),
            )


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------


def _literal_type(literal: Literal) -> FieldType:
    return {
        "integer": FieldType.INTEGER,
        "decimal": FieldType.DECIMAL,
        "string": FieldType.STRING,
        "boolean": FieldType.BOOLEAN,
        "null": FieldType.NULL,
    }.get(literal.kind, FieldType.ANY)


def _as_field_type(value) -> Optional[FieldType]:
    if value is None:
        return None
    if isinstance(value, FieldType):
        return value
    return None


def _numeric_result(left: Optional[FieldType], right: Optional[FieldType]) -> FieldType:
    types = {left, right}
    if FieldType.DECIMAL in types or FieldType.ANY in types:
        return FieldType.DECIMAL if FieldType.ANY not in types else FieldType.ANY
    if FieldType.INTEGER in types:
        return FieldType.INTEGER
    return FieldType.DECIMAL


def _unify_types(left: Optional[FieldType], right: Optional[FieldType]) -> FieldType:
    if left is None:
        return right or FieldType.ANY
    if right is None:
        return left
    if left == right:
        return left
    if left.is_numeric and right.is_numeric:
        return FieldType.DECIMAL
    return FieldType.ANY


def _parse_type_name(name: str) -> Optional[FieldType]:
    mapping = {
        "INTEGER": FieldType.INTEGER,
        "INT": FieldType.INTEGER,
        "DECIMAL": FieldType.DECIMAL,
        "FLOAT": FieldType.DECIMAL,
        "DOUBLE": FieldType.DECIMAL,
        "STRING": FieldType.STRING,
        "TEXT": FieldType.STRING,
        "BOOL": FieldType.BOOLEAN,
        "BOOLEAN": FieldType.BOOLEAN,
        "DATE": FieldType.DATE,
        "TIMESTAMP": FieldType.DATE,
    }
    return mapping.get(name.upper())


def _merge_span(left: Expr, right: Expr) -> Optional[SourceSpan]:
    if left.span is None:
        return right.span
    if right.span is None:
        return left.span
    return left.span.merge(right.span)


def _contains_aggregate(expr: Optional[Expr]) -> bool:
    if expr is None:
        return False
    if isinstance(expr, FunctionCall):
        if expr.name.upper() in AGGREGATE_FUNCTIONS:
            return True
        return any(_contains_aggregate(a) for a in expr.args)
    if isinstance(expr, BinaryOp):
        return _contains_aggregate(expr.left) or _contains_aggregate(expr.right)
    if isinstance(expr, UnaryOp):
        return _contains_aggregate(expr.operand)
    if isinstance(expr, BetweenExpr):
        return any(
            _contains_aggregate(e) for e in (expr.operand, expr.low, expr.high)
        )
    if isinstance(expr, InListExpr):
        return _contains_aggregate(expr.operand) or any(
            _contains_aggregate(o) for o in expr.options
        )
    if isinstance(expr, LikeExpr):
        return _contains_aggregate(expr.operand) or _contains_aggregate(expr.pattern)
    if isinstance(expr, IsNullExpr):
        return _contains_aggregate(expr.operand)
    if isinstance(expr, CaseExpr):
        if expr.operand is not None and _contains_aggregate(expr.operand):
            return True
        for when in expr.whens:
            if _contains_aggregate(when.condition) or _contains_aggregate(when.result):
                return True
        return _contains_aggregate(expr.default)
    return False


def _expr_key(expr: Optional[Expr]) -> str:
    """A structural key used to compare GROUP BY terms against select items."""
    from ..ast import Identifier

    if expr is None:
        return ""
    if isinstance(expr, Identifier) and expr.parts:
        return expr.parts[-1].lower()
    # Fallback: lexeme-based
    try:
        return str(expr).lower()
    except Exception:
        return ""


def analyze(statement: Statement, catalog: Optional[Catalog] = None) -> SemanticResult:
    """Convenience wrapper around :class:`SemanticAnalyzer`."""
    return SemanticAnalyzer(catalog).analyze(statement)
