"""Tests for the SQL parser and AST construction."""
from optisql.ast import (
    BetweenExpr,
    BinaryOp,
    CaseExpr,
    CreateTableStatement,
    DeleteStatement,
    FunctionCall,
    Identifier,
    InListExpr,
    InsertStatement,
    IsNullExpr,
    LikeExpr,
    Literal,
    SelectStatement,
    Star,
    UnaryOp,
    UpdateStatement,
)
from optisql.diagnostics import DiagnosticBag
from optisql.parser import Parser, parse_sql, parse_statement


def parse_ok(source):
    bag = DiagnosticBag()
    stmt = parse_statement(source, bag)
    assert not bag.has_errors, [d.message for d in bag.errors]
    return stmt


def test_select_star():
    stmt = parse_ok("SELECT * FROM users")
    assert isinstance(stmt, SelectStatement)
    assert isinstance(stmt.items[0].expr, Star)
    assert stmt.from_table.name == "users"


def test_select_columns_with_alias():
    stmt = parse_ok("SELECT name AS n, age FROM users")
    assert stmt.items[0].alias == "n"
    assert isinstance(stmt.items[0].expr, Identifier)
    assert stmt.items[0].expr.name == "name"
    assert stmt.items[1].alias is None


def test_select_implicit_alias():
    stmt = parse_ok("SELECT name n FROM users")
    assert stmt.items[0].alias == "n"


def test_where_comparison():
    stmt = parse_ok("SELECT name FROM users WHERE age >= 18")
    where = stmt.where
    assert isinstance(where, BinaryOp)
    assert where.op == ">="
    assert isinstance(where.left, Identifier)
    assert isinstance(where.right, Literal)
    assert where.right.value == 18


def test_boolean_precedence_and_before_or():
    stmt = parse_ok("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3")
    # AND binds tighter than OR: (a=1) OR ((b=2) AND (c=3))
    assert stmt.where.op == "OR"
    assert stmt.where.right.op == "AND"


def test_parentheses_override_precedence():
    stmt = parse_ok("SELECT * FROM t WHERE (a = 1 OR b = 2) AND c = 3")
    assert stmt.where.op == "AND"
    assert stmt.where.left.op == "OR"


def test_not_operator():
    stmt = parse_ok("SELECT * FROM t WHERE NOT active = TRUE")
    assert isinstance(stmt.where, UnaryOp)
    assert stmt.where.op == "NOT"


def test_arithmetic_precedence():
    stmt = parse_ok("SELECT a + b * c FROM t")
    expr = stmt.items[0].expr
    assert expr.op == "+"
    assert expr.right.op == "*"


def test_unary_minus():
    stmt = parse_ok("SELECT -5 FROM t")
    assert isinstance(stmt.items[0].expr, UnaryOp)
    assert stmt.items[0].expr.op == "-"


def test_aggregate_functions():
    stmt = parse_ok("SELECT COUNT(*), SUM(age), AVG(salary) FROM users")
    count = stmt.items[0].expr
    assert isinstance(count, FunctionCall)
    assert count.name == "COUNT"
    assert isinstance(count.args[0], Star)
    assert stmt.has_aggregates


def test_count_distinct():
    stmt = parse_ok("SELECT COUNT(DISTINCT country) FROM users")
    fn = stmt.items[0].expr
    assert fn.distinct is True


def test_group_by_having():
    stmt = parse_ok(
        "SELECT country, COUNT(*) FROM users GROUP BY country "
        "HAVING COUNT(*) > 5"
    )
    assert len(stmt.group_by) == 1
    assert stmt.having is not None


def test_order_by_asc_desc():
    stmt = parse_ok("SELECT * FROM t ORDER BY a ASC, b DESC")
    assert len(stmt.order_by) == 2
    assert stmt.order_by[0].descending is False
    assert stmt.order_by[1].descending is True


def test_limit_offset():
    stmt = parse_ok("SELECT * FROM t LIMIT 10 OFFSET 5")
    assert stmt.limit.value == 10
    assert stmt.offset.value == 5


def test_distinct_flag():
    stmt = parse_ok("SELECT DISTINCT city FROM users")
    assert stmt.distinct is True


def test_between_expression():
    stmt = parse_ok("SELECT * FROM t WHERE age BETWEEN 18 AND 65")
    assert isinstance(stmt.where, BetweenExpr)


def test_in_list_expression():
    stmt = parse_ok("SELECT * FROM t WHERE country IN ('IN', 'US')")
    assert isinstance(stmt.where, InListExpr)
    assert len(stmt.where.options) == 2


def test_not_in_expression():
    stmt = parse_ok("SELECT * FROM t WHERE country NOT IN ('IN')")
    assert stmt.where.negated is True


def test_like_expression():
    stmt = parse_ok("SELECT * FROM t WHERE name LIKE 'A%'")
    assert isinstance(stmt.where, LikeExpr)


def test_is_null_expression():
    stmt = parse_ok("SELECT * FROM t WHERE email IS NOT NULL")
    assert isinstance(stmt.where, IsNullExpr)
    assert stmt.where.negated is True


def test_case_expression():
    stmt = parse_ok(
        "SELECT CASE WHEN age >= 18 THEN 'adult' ELSE 'minor' END FROM t"
    )
    assert isinstance(stmt.items[0].expr, CaseExpr)


def test_qualified_column_reference():
    stmt = parse_ok("SELECT u.name FROM users u WHERE u.age > 5")
    col = stmt.items[0].expr
    assert col.qualifier == "u"
    assert col.name == "name"


def test_join_clause():
    stmt = parse_ok(
        "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id"
    )
    assert len(stmt.joins) == 1
    assert stmt.joins[0].table.name == "orders"
    assert stmt.joins[0].condition is not None


def test_insert_statement():
    stmt = parse_ok("INSERT INTO users (name, age) VALUES ('A', 1), ('B', 2)")
    assert isinstance(stmt, InsertStatement)
    assert stmt.columns == ["name", "age"]
    assert len(stmt.rows) == 2


def test_update_statement():
    stmt = parse_ok("UPDATE users SET age = 30 WHERE name = 'A'")
    assert isinstance(stmt, UpdateStatement)
    assert stmt.assignments[0].field == "age"
    assert stmt.where is not None


def test_delete_statement():
    stmt = parse_ok("DELETE FROM users WHERE age < 18")
    assert isinstance(stmt, DeleteStatement)
    assert stmt.where is not None


def test_create_table_statement():
    stmt = parse_ok("CREATE TABLE users (id INTEGER, name STRING)")
    assert isinstance(stmt, CreateTableStatement)
    assert len(stmt.columns) == 2
    assert stmt.columns[0].type_name == "INTEGER"


def test_comments_are_skipped():
    stmt = parse_ok("SELECT /* c */ name -- trailing\nFROM users")
    assert stmt.from_table.name == "users"


def test_multiple_statements_with_semicolons():
    stmts = parse_sql("SELECT * FROM a; SELECT * FROM b;")
    assert len(stmts) == 2


# -- Error cases -----------------------------------------------------------


def test_missing_from_table_reports_error():
    bag = DiagnosticBag()
    Parser("SELECT * FROM", bag).parse()
    assert bag.has_errors
    assert any(d.code == "E2001" for d in bag.errors)


def test_missing_rparen_reports_error():
    bag = DiagnosticBag()
    Parser("SELECT * FROM t WHERE (a = 1", bag).parse()
    assert bag.has_errors
    assert any(d.code == "E2001" for d in bag.errors)


def test_unsupported_statement_reports_error():
    bag = DiagnosticBag()
    Parser("DROP TABLE users", bag).parse_statements()
    assert bag.has_errors
    assert any(d.code == "E2003" for d in bag.errors)


def test_trailing_input_reports_error():
    bag = DiagnosticBag()
    Parser("SELECT * FROM t garbage here", bag).parse()
    assert bag.has_errors
    assert any(d.code == "E2002" for d in bag.errors)


def test_join_requires_on():
    bag = DiagnosticBag()
    Parser("SELECT * FROM a JOIN b", bag).parse()
    assert bag.has_errors
    assert any(d.code == "E2004" for d in bag.errors)


def test_parse_error_includes_location():
    bag = DiagnosticBag()
    Parser("SELECT * FROM t WHERE (a = 1", bag).parse()
    err = bag.errors[0]
    assert err.span is not None
    assert err.span.start.line == 1
