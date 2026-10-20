"""Semantic analysis: name resolution, type checking, and validity rules."""
from .analyzer import SemanticAnalyzer, SemanticResult, analyze

__all__ = ["SemanticAnalyzer", "SemanticResult", "analyze"]
