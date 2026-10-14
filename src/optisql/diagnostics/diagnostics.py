"""Structured diagnostics with error codes and source locations.

Every compiler phase reports problems through this module so that errors are
consistent, carry a machine-readable code, and point at a source location when
one is available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional


class Severity(str, Enum):
    """Severity level of a diagnostic."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class SourceLocation:
    """A single position in the source text.

    ``offset`` is a 0-based character offset, ``line`` and ``column`` are
    1-based so they read naturally in error messages.
    """

    offset: int
    line: int
    column: int


@dataclass(frozen=True)
class SourceSpan:
    """A half-open range ``[start, end)`` in the source text."""

    start: SourceLocation
    end: SourceLocation

    @classmethod
    def point(cls, location: SourceLocation) -> "SourceSpan":
        return cls(location, location)

    def merge(self, other: "SourceSpan") -> "SourceSpan":
        """Return the smallest span covering both spans."""
        start = self.start if self.start.offset <= other.start.offset else other.start
        end = self.end if self.end.offset >= other.end.offset else other.end
        return SourceSpan(start, end)


@dataclass(frozen=True)
class Diagnostic:
    """A single compiler message."""

    code: str
    message: str
    severity: Severity = Severity.ERROR
    span: Optional[SourceSpan] = None

    def format(self, source: Optional[str] = None) -> str:
        """Render a human-readable message, optionally with a source snippet."""
        prefix = f"{self.severity.value}[{self.code}]"
        if self.span is None:
            return f"{prefix}: {self.message}"

        loc = self.span.start
        header = f"{prefix}: {self.message} (line {loc.line}, column {loc.column})"
        if source is None:
            return header

        lines = source.splitlines()
        if 1 <= loc.line <= len(lines):
            src_line = lines[loc.line - 1]
            caret_pad = " " * max(0, loc.column - 1)
            width = max(1, self.span.end.offset - self.span.start.offset)
            caret = caret_pad + "^" * width
            return f"{header}\n  {src_line}\n  {caret}"
        return header


class DiagnosticError(Exception):
    """Raised when a phase chooses to abort on errors."""

    def __init__(self, diagnostics: List[Diagnostic]):
        self.diagnostics = list(diagnostics)
        message = "\n".join(d.format() for d in self.diagnostics)
        super().__init__(message)


class DiagnosticBag:
    """Accumulates diagnostics produced during a compilation phase."""

    def __init__(self) -> None:
        self._items: List[Diagnostic] = []

    def error(
        self,
        code: str,
        message: str,
        span: Optional[SourceSpan] = None,
    ) -> Diagnostic:
        return self._add(code, message, Severity.ERROR, span)

    def warning(
        self,
        code: str,
        message: str,
        span: Optional[SourceSpan] = None,
    ) -> Diagnostic:
        return self._add(code, message, Severity.WARNING, span)

    def info(
        self,
        code: str,
        message: str,
        span: Optional[SourceSpan] = None,
    ) -> Diagnostic:
        return self._add(code, message, Severity.INFO, span)

    def add(self, diagnostic: Diagnostic) -> Diagnostic:
        self._items.append(diagnostic)
        return diagnostic

    def extend(self, diagnostics: Iterable[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            self._items.append(diagnostic)

    def _add(
        self,
        code: str,
        message: str,
        severity: Severity,
        span: Optional[SourceSpan],
    ) -> Diagnostic:
        diagnostic = Diagnostic(
            code=code, message=message, severity=severity, span=span
        )
        self._items.append(diagnostic)
        return diagnostic

    @property
    def items(self) -> List[Diagnostic]:
        return list(self._items)

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self._items if d.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self._items if d.severity == Severity.WARNING]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == Severity.ERROR for d in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def raise_if_errors(self) -> None:
        if self.has_errors:
            raise DiagnosticError(self.errors)
