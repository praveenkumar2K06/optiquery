"""Tests for the lexical analyzer."""
import pytest

from optisql.diagnostics import DiagnosticBag
from optisql.lexer import Lexer, TokenType, tokenize


def types(source):
    return [t.type for t in tokenize(source) if t.type is not TokenType.EOF]


def values(source):
    return [t.value for t in tokenize(source) if t.type is not TokenType.EOF]


def test_empty_input_yields_eof_only():
    tokens = tokenize("")
    assert len(tokens) == 1
    assert tokens[0].type is TokenType.EOF


def test_select_star():
    toks = tokenize("SELECT * FROM users")
    assert [t.type for t in toks] == [
        TokenType.SELECT,
        TokenType.STAR,
        TokenType.FROM,
        TokenType.IDENTIFIER,
        TokenType.EOF,
    ]


def test_keywords_are_case_insensitive():
    uppercase = types("SELECT FROM WHERE")
    lowercase = types("select from where")
    mixed = types("SeLeCt FrOm WhErE")
    assert uppercase == lowercase == mixed


def test_all_declared_keywords_lex():
    text = (
        "SELECT FROM WHERE AND OR NOT AS ORDER BY GROUP HAVING LIMIT OFFSET "
        "DISTINCT INSERT UPDATE DELETE CREATE TABLE JOIN INNER LEFT RIGHT "
        "OUTER ON ASC DESC IS IN LIKE BETWEEN EXISTS CASE WHEN THEN ELSE END"
    )
    toks = tokenize(text)
    assert all(t.type is not TokenType.IDENTIFIER for t in toks[:-1])


def test_integer_and_decimal_literals():
    assert values("42") == [42]
    assert types("42") == [TokenType.INTEGER]
    assert values("3.14") == [3.14]
    assert types("3.14") == [TokenType.DECIMAL]
    assert values("1e3") == [1000.0]
    assert values("1.5e-2") == [0.015]


def test_qualified_identifier_not_confused_with_decimal():
    toks = tokenize("users.id")
    assert [t.type for t in toks] == [
        TokenType.IDENTIFIER,
        TokenType.DOT,
        TokenType.IDENTIFIER,
        TokenType.EOF,
    ]
    assert values("users.id") == ["users", ".", "id"]


def test_string_literals_and_escaping():
    assert values("'hello world'") == ["hello world"]
    assert values("'it''s'") == ["it's"]
    assert values(r"'line\nbreak'") == ["line\nbreak"]
    assert values(r"'tab\there'") == ["tab\there"]


def test_boolean_and_null_literals():
    assert types("TRUE") == [TokenType.BOOLEAN]
    assert values("TRUE") == [True]
    assert values("false") == [False]
    assert types("NULL") == [TokenType.NULL]
    assert values("null") == [None]


def test_comparison_operators():
    assert types("= != <> < > <= >=") == [
        TokenType.EQ,
        TokenType.NEQ,
        TokenType.NEQ,
        TokenType.LT,
        TokenType.GT,
        TokenType.LTE,
        TokenType.GTE,
    ]


def test_arithmetic_operators():
    assert types("+ - * / %") == [
        TokenType.PLUS,
        TokenType.MINUS,
        TokenType.STAR,
        TokenType.SLASH,
        TokenType.PERCENT,
    ]


def test_punctuation():
    assert types("( ) , . ;") == [
        TokenType.LPAREN,
        TokenType.RPAREN,
        TokenType.COMMA,
        TokenType.DOT,
        TokenType.SEMICOLON,
    ]


def test_line_comment_is_ignored():
    toks = tokenize("SELECT 1 -- this is a comment\nFROM t")
    assert [t.type for t in toks] == [
        TokenType.SELECT,
        TokenType.INTEGER,
        TokenType.FROM,
        TokenType.IDENTIFIER,
        TokenType.EOF,
    ]


def test_block_comment_is_ignored():
    toks = tokenize("SELECT /* comment */ 1")
    assert [t.type for t in toks] == [
        TokenType.SELECT,
        TokenType.INTEGER,
        TokenType.EOF,
    ]


def test_quoted_identifiers():
    assert values('"select"') == ["select"]
    assert values("`my field`") == ["my field"]


def test_line_and_column_tracking():
    toks = tokenize("SELECT\n  name\nFROM t")
    name = toks[1]
    assert name.lexeme == "name"
    assert name.span.start.line == 2
    assert name.span.start.column == 3


def test_invalid_character_reports_diagnostic():
    bag = DiagnosticBag()
    tokenize("SELECT @x", bag)
    assert bag.has_errors
    err = bag.errors[0]
    assert err.code == "E1001"
    assert err.span is not None
    assert err.span.start.column == 8


def test_unterminated_string_reports_diagnostic():
    bag = DiagnosticBag()
    tokenize("SELECT 'abc", bag)
    assert bag.has_errors
    assert bag.errors[0].code == "E1002"


def test_unterminated_block_comment_reports_diagnostic():
    bag = DiagnosticBag()
    tokenize("SELECT /* abc", bag)
    assert bag.has_errors
    assert bag.errors[0].code == "E1004"


def test_lexeme_is_preserved():
    toks = tokenize("SELECT name")
    assert [t.lexeme for t in toks] == ["SELECT", "name", ""]


def test_span_offsets():
    toks = tokenize("SELECT name")
    name = toks[1]
    assert name.span.start.offset == 7
    assert name.span.end.offset == 11
