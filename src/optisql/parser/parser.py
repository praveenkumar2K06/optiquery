"""Recursive-descent parser for the OptiQuery SQL subset.

Grammar (EBNF)::

    statement        := select_stmt | insert_stmt | update_stmt
                      | delete_stmt | create_table_stmt
    select_stmt      := SELECT [DISTINCT] select_list
                        [FROM table_ref {join_clause}]
                        [WHERE expr]
                        [GROUP BY expr_list]
                        [HAVING expr]
                        [ORDER BY order_item {"," order_item}]
                        [LIMIT expr] [OFFSET expr]
    select_list      := "*" | select_item {"," select_item}
    select_item      := expr [AS? alias]
    table_ref        := identifier ["." identifier] [AS? alias]
    join_clause      := [INNER|LEFT|RIGHT [OUTER]] JOIN table_ref ON expr
    order_item       := expr [ASC|DESC]
    expr             := or_expr
    or_expr          := and_expr {OR and_expr}
    and_expr         := not_expr {AND not_expr}
    not_expr         := NOT not_expr | comparison
    comparison       := additive [ (=|!=|<>|<|>|<=|>=) additive
                        | IS [NOT] NULL
                        | [NOT] IN "(" expr_list ")"
                        | [NOT] LIKE additive
                        | [NOT] BETWEEN additive AND additive ]
    additive         := multiplicative { (+|-) multiplicative }
    multiplicative   := unary { (*|/|%) unary }
    unary            := (-|+) unary | primary
    primary          := literal | function_call | identifier | "(" expr ")" | case_expr

Expression parsing uses precedence climbing to encode the binding strengths
above without regular expressions or ad hoc string rewriting.
"""
from __future__ import annotations

from typing import List, Optional

from ..ast import (
    Assignment,
    BetweenExpr,
    BinaryOp,
    CaseExpr,
    CaseWhen,
    ColumnDef,
    CreateTableStatement,
    DeleteStatement,
    Expr,
    FunctionCall,
    Identifier,
    InListExpr,
    InsertStatement,
    IsNullExpr,
    JoinClause,
    LikeExpr,
    Literal,
    OrderByItem,
    SelectItem,
    SelectStatement,
    Star,
    Statement,
    TableRef,
    UnaryOp,
    UpdateStatement,
)
from ..diagnostics import DiagnosticBag, SourceSpan
from ..lexer import Lexer, Token, TokenType


class Parser:
    """Parses SQL text into AST statements."""

    def __init__(self, source: str, diagnostics: Optional[DiagnosticBag] = None):
        self.source = source
        self.diagnostics = (
            diagnostics if diagnostics is not None else DiagnosticBag()
        )
        # Lex up-front so lexical diagnostics are collected alongside syntax.
        self.tokens = Lexer(source, self.diagnostics).tokenize()
        self.pos = 0

    # -- Token helpers -----------------------------------------------------

    def _peek(self, ahead: int = 0) -> Token:
        index = self.pos + ahead
        if index >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[index]

    def _current(self) -> Token:
        return self._peek(0)

    def _check(self, *types: TokenType) -> bool:
        return self._current().type in types

    def _advance(self) -> Token:
        token = self._current()
        if token.type is not TokenType.EOF:
            self.pos += 1
        return token

    def _match(self, *types: TokenType) -> Optional[Token]:
        if self._check(*types):
            return self._advance()
        return None

    def _expect(self, token_type: TokenType, what: str) -> Token:
        if self._check(token_type):
            return self._advance()
        token = self._current()
        self._error(
            "E2001",
            f"Expected {what} but found {self._describe(token)}",
            token.span,
        )
        return token

    @staticmethod
    def _describe(token: Token) -> str:
        if token.type is TokenType.EOF:
            return "end of input"
        return repr(token.lexeme or token.value)

    def _error(self, code: str, message: str, span: Optional[SourceSpan]) -> None:
        self.diagnostics.error(code, message, span)

    def _span_from(self, start: Token) -> SourceSpan:
        end = self.tokens[self.pos - 1] if self.pos > 0 else start
        return start.span.merge(end.span)

    # -- Entry points ------------------------------------------------------

    def parse_statements(self) -> List[Statement]:
        """Parse one or more semicolon-separated statements."""
        statements: List[Statement] = []
        while not self._check(TokenType.EOF):
            if self._match(TokenType.SEMICOLON):
                continue
            start_pos = self.pos
            statement = self._parse_statement()
            if statement is not None:
                statements.append(statement)
            # Guard against non-progress on malformed input.
            if self.pos == start_pos:
                self._advance()
        return statements

    def parse(self) -> Optional[Statement]:
        """Parse exactly one statement.

        Diagnostics are recorded in the parser's bag and a best-effort AST is
        returned. Callers that treat any error as fatal should call
        ``diagnostics.raise_if_errors()`` afterwards.
        """
        if self._check(TokenType.EOF):
            self._error("E2000", "Empty statement", self._current().span)
            return None
        statement = self._parse_statement()
        self._match(TokenType.SEMICOLON)
        if not self._check(TokenType.EOF):
            token = self._current()
            self._error(
                "E2002",
                f"Unexpected trailing input {self._describe(token)}",
                token.span,
            )
        return statement

    def _parse_statement(self) -> Optional[Statement]:
        token = self._current()
        if token.type is TokenType.SELECT:
            return self._parse_select()
        if token.type is TokenType.INSERT:
            return self._parse_insert()
        if token.type is TokenType.UPDATE:
            return self._parse_update()
        if token.type is TokenType.DELETE:
            return self._parse_delete()
        if token.type is TokenType.CREATE:
            return self._parse_create_table()

        self._error(
            "E2003",
            f"Unsupported or unexpected statement starting with "
            f"{self._describe(token)}",
            token.span,
        )
        # Skip to the next statement boundary to allow recovery.
        while not self._check(TokenType.EOF, TokenType.SEMICOLON):
            self._advance()
        self._match(TokenType.SEMICOLON)
        return None

    # -- SELECT ------------------------------------------------------------

    def _parse_select(self) -> SelectStatement:
        start = self._expect(TokenType.SELECT, "SELECT")
        distinct = self._match(TokenType.DISTINCT) is not None

        items = self._parse_select_list()

        from_table: Optional[TableRef] = None
        joins: List[JoinClause] = []
        if self._match(TokenType.FROM):
            from_table = self._parse_table_ref()
            # Support implicit cross joins: FROM a, b
            while self._match(TokenType.COMMA):
                next_table = self._parse_table_ref()
                joins.append(
                    JoinClause(
                        join_type="CROSS",
                        table=next_table,
                        condition=None,
                        span=next_table.span,
                    )
                )
            while self._check(
                TokenType.JOIN,
                TokenType.INNER,
                TokenType.LEFT,
                TokenType.RIGHT,
            ):
                joins.append(self._parse_join())

        where = None
        if self._match(TokenType.WHERE):
            where = self._parse_expr()

        group_by: List[Expr] = []
        if self._match(TokenType.GROUP):
            self._expect(TokenType.BY, "BY")
            group_by = self._parse_expr_list()

        having = None
        if self._match(TokenType.HAVING):
            having = self._parse_expr()

        order_by: List[OrderByItem] = []
        if self._match(TokenType.ORDER):
            self._expect(TokenType.BY, "BY")
            order_by = self._parse_order_by()

        limit = None
        offset = None
        if self._match(TokenType.LIMIT):
            limit = self._parse_expr()
        if self._match(TokenType.OFFSET):
            offset = self._parse_expr()

        return SelectStatement(
            items=items,
            from_table=from_table,
            joins=joins,
            where=where,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
            distinct=distinct,
            span=self._span_from(start),
        )

    def _parse_select_list(self) -> List[SelectItem]:
        items: List[SelectItem] = []
        if self._check(TokenType.STAR):
            start = self._advance()
            items.append(SelectItem(expr=Star(span=start.span), span=start.span))
        else:
            items.append(self._parse_select_item())
        while self._match(TokenType.COMMA):
            items.append(self._parse_select_item())
        return items

    def _parse_select_item(self) -> SelectItem:
        start = self._current()
        # ``alias.*`` form.
        if (
            self._check(TokenType.IDENTIFIER)
            and self._peek(1).type is TokenType.DOT
            and self._peek(2).type is TokenType.STAR
        ):
            qual = self._advance()
            self._advance()  # dot
            star_tok = self._advance()  # star
            return SelectItem(
                expr=Star(qualifier=qual.value, span=qual.span.merge(star_tok.span)),
                span=self._span_from(start),
            )

        expr = self._parse_expr()
        alias = self._parse_optional_alias()
        return SelectItem(expr=expr, alias=alias, span=self._span_from(start))

    def _parse_optional_alias(self) -> Optional[str]:
        if self._match(TokenType.AS):
            token = self._expect(TokenType.IDENTIFIER, "alias name")
            return token.value
        # Implicit alias: an identifier that is not a clause keyword.
        if self._check(TokenType.IDENTIFIER):
            # Avoid stealing a following table name after FROM.
            nxt = self._peek(1)
            if nxt.type in (
                TokenType.COMMA,
                TokenType.FROM,
            ):
                return self._advance().value
        return None

    def _parse_table_ref(self) -> TableRef:
        start = self._expect(TokenType.IDENTIFIER, "table name")
        name = start.value
        # Support ``schema.table``.
        while self._match(TokenType.DOT):
            nxt = self._expect(TokenType.IDENTIFIER, "table name")
            name += "." + nxt.value

        alias = None
        if self._match(TokenType.AS):
            alias = self._expect(TokenType.IDENTIFIER, "table alias").value
        elif self._check(TokenType.IDENTIFIER):
            alias = self._advance().value
        return TableRef(name=name, alias=alias, span=self._span_from(start))

    def _parse_join(self) -> JoinClause:
        start = self._current()
        join_type = "INNER"
        if self._match(TokenType.LEFT):
            join_type = "LEFT"
            self._match(TokenType.OUTER)
        elif self._match(TokenType.RIGHT):
            join_type = "RIGHT"
            self._match(TokenType.OUTER)
        elif self._match(TokenType.INNER):
            join_type = "INNER"
        self._expect(TokenType.JOIN, "JOIN")
        table = self._parse_table_ref()
        condition = None
        if self._match(TokenType.ON):
            condition = self._parse_expr()
        else:
            self._error("E2004", "JOIN requires an ON condition", self._span_from(start))
        return JoinClause(
            join_type=join_type,
            table=table,
            condition=condition,
            span=self._span_from(start),
        )

    def _parse_order_by(self) -> List[OrderByItem]:
        items: List[OrderByItem] = []
        while True:
            start = self._current()
            expr = self._parse_expr()
            descending = False
            if self._match(TokenType.DESC):
                descending = True
            elif self._match(TokenType.ASC):
                descending = False
            items.append(
                OrderByItem(
                    expr=expr,
                    descending=descending,
                    span=self._span_from(start),
                )
            )
            if not self._match(TokenType.COMMA):
                break
        return items

    def _parse_expr_list(self) -> List[Expr]:
        exprs = [self._parse_expr()]
        while self._match(TokenType.COMMA):
            exprs.append(self._parse_expr())
        return exprs

    # -- Expressions (precedence climbing) ---------------------------------

    def _parse_expr(self) -> Expr:
        return self._parse_or()

    def _parse_or(self) -> Expr:
        start = self._current()
        left = self._parse_and()
        while self._match(TokenType.OR):
            right = self._parse_and()
            left = BinaryOp(op="OR", left=left, right=right, span=self._span_from(start))
        return left

    def _parse_and(self) -> Expr:
        start = self._current()
        left = self._parse_not()
        while self._match(TokenType.AND):
            right = self._parse_not()
            left = BinaryOp(
                op="AND", left=left, right=right, span=self._span_from(start)
            )
        return left

    def _parse_not(self) -> Expr:
        start = self._current()
        if self._match(TokenType.NOT):
            operand = self._parse_not()
            return UnaryOp(op="NOT", operand=operand, span=self._span_from(start))
        return self._parse_comparison()

    _COMPARISON_OPS = {
        TokenType.EQ: "=",
        TokenType.NEQ: "!=",
        TokenType.LT: "<",
        TokenType.GT: ">",
        TokenType.LTE: "<=",
        TokenType.GTE: ">=",
    }

    def _parse_comparison(self) -> Expr:
        start = self._current()
        left = self._parse_additive()

        if self._current().type in self._COMPARISON_OPS:
            op = self._COMPARISON_OPS[self._advance().type]
            right = self._parse_additive()
            return BinaryOp(op=op, left=left, right=right, span=self._span_from(start))

        if self._match(TokenType.IS):
            negated = self._match(TokenType.NOT) is not None
            self._expect(TokenType.NULL, "NULL")
            return IsNullExpr(
                operand=left, negated=negated, span=self._span_from(start)
            )

        negated = False
        if self._check(TokenType.NOT) and self._peek(1).type in (
            TokenType.IN,
            TokenType.LIKE,
            TokenType.BETWEEN,
        ):
            self._advance()
            negated = True

        if self._match(TokenType.IN):
            self._expect(TokenType.LPAREN, "(")
            options = self._parse_expr_list()
            self._expect(TokenType.RPAREN, ")")
            return InListExpr(
                operand=left,
                options=options,
                negated=negated,
                span=self._span_from(start),
            )

        if self._match(TokenType.LIKE):
            pattern = self._parse_additive()
            return LikeExpr(
                operand=left,
                pattern=pattern,
                negated=negated,
                span=self._span_from(start),
            )

        if self._match(TokenType.BETWEEN):
            low = self._parse_additive()
            self._expect(TokenType.AND, "AND")
            high = self._parse_additive()
            return BetweenExpr(
                operand=left,
                low=low,
                high=high,
                negated=negated,
                span=self._span_from(start),
            )

        if negated:
            token = self._current()
            self._error(
                "E2005",
                f"Expected IN, LIKE, or BETWEEN after NOT but found "
                f"{self._describe(token)}",
                token.span,
            )

        return left

    def _parse_additive(self) -> Expr:
        start = self._current()
        left = self._parse_multiplicative()
        while self._check(TokenType.PLUS, TokenType.MINUS):
            op = "+" if self._advance().type is TokenType.PLUS else "-"
            right = self._parse_multiplicative()
            left = BinaryOp(op=op, left=left, right=right, span=self._span_from(start))
        return left

    def _parse_multiplicative(self) -> Expr:
        start = self._current()
        left = self._parse_unary()
        while self._check(TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            token = self._advance()
            op = {
                TokenType.STAR: "*",
                TokenType.SLASH: "/",
                TokenType.PERCENT: "%",
            }[token.type]
            right = self._parse_unary()
            left = BinaryOp(op=op, left=left, right=right, span=self._span_from(start))
        return left

    def _parse_unary(self) -> Expr:
        start = self._current()
        if self._match(TokenType.MINUS):
            operand = self._parse_unary()
            return UnaryOp(op="-", operand=operand, span=self._span_from(start))
        if self._match(TokenType.PLUS):
            return self._parse_unary()
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        token = self._current()

        if token.type is TokenType.INTEGER:
            self._advance()
            return Literal(value=token.value, kind="integer", span=token.span)
        if token.type is TokenType.DECIMAL:
            self._advance()
            return Literal(value=token.value, kind="decimal", span=token.span)
        if token.type is TokenType.STRING:
            self._advance()
            return Literal(value=token.value, kind="string", span=token.span)
        if token.type is TokenType.BOOLEAN:
            self._advance()
            return Literal(value=token.value, kind="boolean", span=token.span)
        if token.type is TokenType.NULL:
            self._advance()
            return Literal(value=None, kind="null", span=token.span)

        if token.type is TokenType.LPAREN:
            self._advance()
            expr = self._parse_expr()
            self._expect(TokenType.RPAREN, ")")
            return expr

        if token.type is TokenType.CASE:
            return self._parse_case()

        if token.type is TokenType.IDENTIFIER:
            return self._parse_identifier_or_call()

        # Aggregate keywords used as function names, e.g. bare COUNT(*) is an
        # identifier followed by parens; recognized below.
        self._error(
            "E2006",
            f"Expected an expression but found {self._describe(token)}",
            token.span,
        )
        self._advance()
        return Literal(value=None, kind="null", span=token.span)

    def _parse_identifier_or_call(self) -> Expr:
        start = self._advance()
        parts = [start.value]

        if self._check(TokenType.LPAREN):
            self._advance()  # (
            distinct = self._match(TokenType.DISTINCT) is not None
            args: List[Expr] = []
            if not self._check(TokenType.RPAREN):
                if self._check(TokenType.STAR):
                    star = self._advance()
                    args.append(Star(span=star.span))
                else:
                    args = self._parse_expr_list()
            self._expect(TokenType.RPAREN, ")")
            return FunctionCall(
                name=start.value.upper(),
                args=args,
                distinct=distinct,
                span=self._span_from(start),
            )

        while self._match(TokenType.DOT):
            nxt = self._expect(TokenType.IDENTIFIER, "identifier")
            parts.append(nxt.value)

        return Identifier(parts=parts, span=self._span_from(start))

    def _parse_case(self) -> CaseExpr:
        start = self._expect(TokenType.CASE, "CASE")
        operand = None
        if not self._check(TokenType.WHEN):
            operand = self._parse_expr()

        whens = []
        while self._match(TokenType.WHEN):
            condition = self._parse_expr()
            self._expect(TokenType.THEN, "THEN")
            result = self._parse_expr()
            whens.append(CaseWhen(condition=condition, result=result))

        default = None
        if self._match(TokenType.ELSE):
            default = self._parse_expr()

        self._expect(TokenType.END, "END")
        return CaseExpr(
            operand=operand,
            whens=whens,
            default=default,
            span=self._span_from(start),
        )

    # -- INSERT / UPDATE / DELETE / CREATE ---------------------------------

    def _parse_insert(self) -> InsertStatement:
        start = self._expect(TokenType.INSERT, "INSERT")
        self._expect(TokenType.INTO, "INTO")
        table = self._parse_table_ref()

        columns: List[str] = []
        if self._match(TokenType.LPAREN):
            if not self._check(TokenType.RPAREN):
                columns.append(self._expect(TokenType.IDENTIFIER, "column").value)
                while self._match(TokenType.COMMA):
                    columns.append(self._expect(TokenType.IDENTIFIER, "column").value)
            self._expect(TokenType.RPAREN, ")")

        self._expect(TokenType.VALUES, "VALUES")
        rows: List[List[Expr]] = []
        while True:
            self._expect(TokenType.LPAREN, "(")
            row = self._parse_expr_list()
            self._expect(TokenType.RPAREN, ")")
            rows.append(row)
            if not self._match(TokenType.COMMA):
                break

        return InsertStatement(
            table=table, columns=columns, rows=rows, span=self._span_from(start)
        )

    def _parse_update(self) -> UpdateStatement:
        start = self._expect(TokenType.UPDATE, "UPDATE")
        table = self._parse_table_ref()
        self._expect(TokenType.SET, "SET")

        assignments: List[Assignment] = []
        while True:
            field_tok = self._expect(TokenType.IDENTIFIER, "field name")
            self._expect(TokenType.EQ, "=")
            value = self._parse_expr()
            assignments.append(
                Assignment(
                    field=field_tok.value,
                    value=value,
                    span=field_tok.span.merge(value.span or field_tok.span),
                )
            )
            if not self._match(TokenType.COMMA):
                break

        where = None
        if self._match(TokenType.WHERE):
            where = self._parse_expr()

        return UpdateStatement(
            table=table,
            assignments=assignments,
            where=where,
            span=self._span_from(start),
        )

    def _parse_delete(self) -> DeleteStatement:
        start = self._expect(TokenType.DELETE, "DELETE")
        self._expect(TokenType.FROM, "FROM")
        table = self._parse_table_ref()
        where = None
        if self._match(TokenType.WHERE):
            where = self._parse_expr()
        return DeleteStatement(
            table=table, where=where, span=self._span_from(start)
        )

    def _parse_create_table(self) -> CreateTableStatement:
        start = self._expect(TokenType.CREATE, "CREATE")
        self._expect(TokenType.TABLE, "TABLE")
        table = self._parse_table_ref()

        columns: List[ColumnDef] = []
        self._expect(TokenType.LPAREN, "(")
        if not self._check(TokenType.RPAREN):
            columns.append(self._parse_column_def())
            while self._match(TokenType.COMMA):
                columns.append(self._parse_column_def())
        self._expect(TokenType.RPAREN, ")")

        return CreateTableStatement(
            table=table, columns=columns, span=self._span_from(start)
        )

    def _parse_column_def(self) -> ColumnDef:
        start = self._current()
        name_tok = self._expect(TokenType.IDENTIFIER, "column name")
        type_tok = self._expect(TokenType.IDENTIFIER, "column type")
        return ColumnDef(
            name=name_tok.value,
            type_name=type_tok.value.upper(),
            span=self._span_from(start),
        )


def parse_statement(
    source: str, diagnostics: Optional[DiagnosticBag] = None
) -> Optional[Statement]:
    """Parse a single SQL statement (best effort; diagnostics are recorded)."""
    return Parser(source, diagnostics).parse()


def parse_sql(
    source: str, diagnostics: Optional[DiagnosticBag] = None
) -> List[Statement]:
    """Parse one or more SQL statements from ``source``."""
    return Parser(source, diagnostics).parse_statements()


def parse_or_raise(source: str) -> Statement:
    """Parse exactly one statement, raising on any diagnostic error."""
    bag = DiagnosticBag()
    statement = Parser(source, bag).parse()
    bag.raise_if_errors()
    assert statement is not None
    return statement
