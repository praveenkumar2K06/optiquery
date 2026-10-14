"""Hand-written lexical analyzer for the supported SQL subset.

The lexer walks the source text once, producing a list of :class:`Token`
objects terminated by an ``EOF`` token. Invalid input produces structured
diagnostics with line/column information instead of raising immediately, so a
caller can collect every lexical problem in a single pass.
"""
from __future__ import annotations

from typing import List, Optional

from ..diagnostics import DiagnosticBag, SourceLocation, SourceSpan
from .tokens import KEYWORDS, Token, TokenType

#: Single-character punctuation and operator tokens.
_SIMPLE_TOKENS = {
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    ",": TokenType.COMMA,
    ".": TokenType.DOT,
    ";": TokenType.SEMICOLON,
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "*": TokenType.STAR,
    "/": TokenType.SLASH,
    "%": TokenType.PERCENT,
}

_OPERATOR_TOKENS = {
    "=": TokenType.EQ,
    "!=": TokenType.NEQ,
    "<>": TokenType.NEQ,
    "<": TokenType.LT,
    ">": TokenType.GT,
    "<=": TokenType.LTE,
    ">=": TokenType.GTE,
}


class Lexer:
    """Converts SQL source text into a list of tokens."""

    def __init__(self, source: str, diagnostics: Optional[DiagnosticBag] = None):
        self.source = source
        self.pos = 0
        self.line = 1
        self.column = 1
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self._tokens: List[Token] = []

    # -- Public API --------------------------------------------------------

    def tokenize(self) -> List[Token]:
        """Scan the entire source and return the token list (ending in EOF)."""
        while not self._at_end():
            self._scan_token()
        self._tokens.append(
            Token(
                type=TokenType.EOF,
                value=None,
                lexeme="",
                span=SourceSpan.point(self._location()),
            )
        )
        return self._tokens

    # -- Character helpers -------------------------------------------------

    def _at_end(self) -> bool:
        return self.pos >= len(self.source)

    def _peek(self, ahead: int = 0) -> str:
        index = self.pos + ahead
        if index >= len(self.source):
            return "\0"
        return self.source[index]

    def _advance(self) -> str:
        char = self.source[self.pos]
        self.pos += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def _location(self) -> SourceLocation:
        return SourceLocation(offset=self.pos, line=self.line, column=self.column)

    # -- Scanning ----------------------------------------------------------

    def _scan_token(self) -> None:
        char = self._peek()

        if char in " \t\r\n":
            self._advance()
            return

        # Comments: -- line and /* block */
        if char == "-" and self._peek(1) == "-":
            self._skip_line_comment()
            return
        if char == "/" and self._peek(1) == "*":
            self._skip_block_comment()
            return

        if char == "'":
            self._scan_string()
            return

        if char == '"' or char == "`":
            self._scan_quoted_identifier(char)
            return

        if char.isdigit():
            self._scan_number()
            return

        if char.isalpha() or char == "_":
            self._scan_identifier()
            return

        # Two-character operators first.
        two = char + self._peek(1)
        if two in ("!=", "<>", "<=", ">="):
            start = self._location()
            self._advance()
            self._advance()
            self._emit(_OPERATOR_TOKENS[two], two, start)
            return

        if char in _OPERATOR_TOKENS:
            start = self._location()
            self._advance()
            self._emit(_OPERATOR_TOKENS[char], char, start)
            return

        if char in _SIMPLE_TOKENS:
            start = self._location()
            self._advance()
            self._emit(_SIMPLE_TOKENS[char], char, start)
            return

        # Anything else is an invalid character.
        start = self._location()
        self._advance()
        self.diagnostics.error(
            "E1001",
            f"Unexpected character {char!r}",
            SourceSpan(start, self._location()),
        )

    def _scan_identifier(self) -> None:
        start = self._location()
        while not self._at_end() and (
            self._peek().isalnum() or self._peek() == "_"
        ):
            self._advance()
        text = self.source[start.offset : self.pos]

        upper = text.upper()
        if upper == "NULL":
            self._emit(TokenType.NULL, None, start)
        elif upper == "TRUE":
            self._emit(TokenType.BOOLEAN, True, start)
        elif upper == "FALSE":
            self._emit(TokenType.BOOLEAN, False, start)
        elif upper in KEYWORDS:
            self._emit(KEYWORDS[upper], upper, start)
        else:
            self._emit(TokenType.IDENTIFIER, text, start)

    def _scan_number(self) -> None:
        start = self._location()
        is_decimal = False
        while not self._at_end() and self._peek().isdigit():
            self._advance()

        # A fractional part is only consumed if a digit follows the dot, so
        # that ``t1.id`` is not mistaken for a decimal literal.
        if self._peek() == "." and self._peek(1).isdigit():
            is_decimal = True
            self._advance()  # consume '.'
            while not self._at_end() and self._peek().isdigit():
                self._advance()

        # Scientific notation.
        if self._peek() in ("e", "E"):
            lookahead = self._peek(1)
            if lookahead.isdigit() or (
                lookahead in ("+", "-") and self._peek(2).isdigit()
            ):
                is_decimal = True
                self._advance()  # e/E
                if self._peek() in ("+", "-"):
                    self._advance()
                while not self._at_end() and self._peek().isdigit():
                    self._advance()

        text = self.source[start.offset : self.pos]
        if is_decimal:
            self._emit(TokenType.DECIMAL, float(text), start)
        else:
            self._emit(TokenType.INTEGER, int(text), start)

    def _scan_string(self) -> None:
        start = self._location()
        self._advance()  # opening quote
        chars: List[str] = []
        while not self._at_end():
            if self._peek() == "'":
                # A doubled quote is an escaped quote (SQL standard); a single
                # quote terminates the literal.
                if self._peek(1) == "'":
                    self._advance()
                    self._advance()
                    chars.append("'")
                    continue
                break
            if self._peek() == "\\":
                self._advance()
                if self._at_end():
                    break
                chars.append(self._unescape(self._advance()))
            else:
                chars.append(self._advance())

        if self._at_end():
            self.diagnostics.error(
                "E1002",
                "Unterminated string literal",
                SourceSpan(start, self._location()),
            )
            self._emit(TokenType.STRING, "".join(chars), start)
            return

        self._advance()  # closing quote
        self._emit(TokenType.STRING, "".join(chars), start)

    def _scan_quoted_identifier(self, quote: str) -> None:
        start = self._location()
        self._advance()  # opening quote
        chars: List[str] = []
        while not self._at_end() and self._peek() != quote:
            if self._peek() == quote and self._peek(1) == quote:
                # Escaped quote (doubled).
                self._advance()
                chars.append(self._advance())
            else:
                chars.append(self._advance())

        if self._at_end():
            self.diagnostics.error(
                "E1003",
                f"Unterminated quoted identifier starting with {quote}",
                SourceSpan(start, self._location()),
            )
            self._emit(TokenType.IDENTIFIER, "".join(chars), start)
            return

        self._advance()  # closing quote
        self._emit(TokenType.IDENTIFIER, "".join(chars), start)

    @staticmethod
    def _unescape(char: str) -> str:
        mapping = {
            "n": "\n",
            "t": "\t",
            "r": "\r",
            "0": "\0",
            "\\": "\\",
            "'": "'",
            '"': '"',
        }
        return mapping.get(char, char)

    def _skip_line_comment(self) -> None:
        while not self._at_end() and self._peek() != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        start = self._location()
        self._advance()  # '/'
        self._advance()  # '*'
        while not self._at_end():
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                return
            self._advance()
        self.diagnostics.error(
            "E1004",
            "Unterminated block comment",
            SourceSpan(start, self._location()),
        )

    def _emit(self, token_type: TokenType, value, start: SourceLocation) -> None:
        lexeme = self.source[start.offset : self.pos]
        self._tokens.append(
            Token(
                type=token_type,
                value=value,
                lexeme=lexeme,
                span=SourceSpan(start, self._location()),
            )
        )


def tokenize(source: str, diagnostics: Optional[DiagnosticBag] = None) -> List[Token]:
    """Convenience wrapper around :class:`Lexer`."""
    return Lexer(source, diagnostics).tokenize()
