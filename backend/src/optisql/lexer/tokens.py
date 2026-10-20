"""Token types and token representation for the OptiQuery SQL dialect."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Optional

from ..diagnostics import SourceSpan


class TokenType(Enum):
    """All token categories recognized by the lexer."""

    # Literals
    IDENTIFIER = auto()
    INTEGER = auto()
    DECIMAL = auto()
    STRING = auto()
    BOOLEAN = auto()
    NULL = auto()

    # Keywords
    SELECT = auto()
    FROM = auto()
    WHERE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    AS = auto()
    ORDER = auto()
    BY = auto()
    GROUP = auto()
    HAVING = auto()
    LIMIT = auto()
    OFFSET = auto()
    DISTINCT = auto()
    INSERT = auto()
    INTO = auto()
    VALUES = auto()
    UPDATE = auto()
    SET = auto()
    DELETE = auto()
    CREATE = auto()
    TABLE = auto()
    JOIN = auto()
    INNER = auto()
    LEFT = auto()
    RIGHT = auto()
    OUTER = auto()
    ON = auto()
    ASC = auto()
    DESC = auto()
    TRUE = auto()
    FALSE = auto()
    IS = auto()
    IN = auto()
    LIKE = auto()
    BETWEEN = auto()
    EXISTS = auto()
    CASE = auto()
    WHEN = auto()
    THEN = auto()
    ELSE = auto()
    END = auto()

    # Operators
    EQ = auto()
    NEQ = auto()
    LT = auto()
    GT = auto()
    LTE = auto()
    GTE = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()

    # Punctuation
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    DOT = auto()
    SEMICOLON = auto()

    # Special
    EOF = auto()


#: Mapping from uppercase keyword text to token type for reserved words.
KEYWORDS = {
    "SELECT": TokenType.SELECT,
    "FROM": TokenType.FROM,
    "WHERE": TokenType.WHERE,
    "AND": TokenType.AND,
    "OR": TokenType.OR,
    "NOT": TokenType.NOT,
    "AS": TokenType.AS,
    "ORDER": TokenType.ORDER,
    "BY": TokenType.BY,
    "GROUP": TokenType.GROUP,
    "HAVING": TokenType.HAVING,
    "LIMIT": TokenType.LIMIT,
    "OFFSET": TokenType.OFFSET,
    "DISTINCT": TokenType.DISTINCT,
    "INSERT": TokenType.INSERT,
    "INTO": TokenType.INTO,
    "VALUES": TokenType.VALUES,
    "UPDATE": TokenType.UPDATE,
    "SET": TokenType.SET,
    "DELETE": TokenType.DELETE,
    "CREATE": TokenType.CREATE,
    "TABLE": TokenType.TABLE,
    "JOIN": TokenType.JOIN,
    "INNER": TokenType.INNER,
    "LEFT": TokenType.LEFT,
    "RIGHT": TokenType.RIGHT,
    "OUTER": TokenType.OUTER,
    "ON": TokenType.ON,
    "ASC": TokenType.ASC,
    "DESC": TokenType.DESC,
    "TRUE": TokenType.TRUE,
    "FALSE": TokenType.FALSE,
    "IS": TokenType.IS,
    "IN": TokenType.IN,
    "LIKE": TokenType.LIKE,
    "BETWEEN": TokenType.BETWEEN,
    "EXISTS": TokenType.EXISTS,
    "CASE": TokenType.CASE,
    "WHEN": TokenType.WHEN,
    "THEN": TokenType.THEN,
    "ELSE": TokenType.ELSE,
    "END": TokenType.END,
}


@dataclass(frozen=True)
class Token:
    """A single lexical token with its source span."""

    type: TokenType
    value: Any
    lexeme: str
    span: SourceSpan

    def is_keyword(self) -> bool:
        return self.type in _KEYWORD_TOKEN_TYPES

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.type.name}, {self.value!r}, line={self.span.start.line})"


_KEYWORD_TOKEN_TYPES = set(KEYWORDS.values()) | {
    TokenType.NULL,
    TokenType.BOOLEAN,
}
