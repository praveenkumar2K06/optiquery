"""Symbol table representing the resolved scope of a query.

A :class:`Scope` maps the names visible in a query (collection names, aliases,
and field names) to :class:`Symbol` objects. Scopes nest, so an inner query
scope can shadow or extend an outer one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from ..metadata import CollectionSchema, FieldType


@dataclass
class Symbol:
    """A named, typed entity visible in a query scope."""

    name: str
    kind: str  # 'collection' | 'field' | 'alias'
    type: FieldType = FieldType.ANY
    #: The collection this field/alias ultimately belongs to.
    source: Optional[str] = None
    #: True when the field's type is not known from schema metadata.
    inferred: bool = False
    #: True when the symbol refers to an alias introduced in the SELECT list.
    is_alias: bool = False
    #: Whether the underlying field is indexed (used by the cost model later).
    indexed: bool = False


class Scope:
    """A named scope of symbols with an optional parent."""

    def __init__(self, name: str = "global", parent: Optional["Scope"] = None):
        self.name = name
        self.parent = parent
        self._symbols: Dict[str, Symbol] = {}

    def define(self, symbol: Symbol) -> None:
        self._symbols[symbol.name.lower()] = symbol

    def has_local(self, name: str) -> bool:
        return name.lower() in self._symbols

    def resolve(self, name: str) -> Optional[Symbol]:
        key = name.lower()
        if key in self._symbols:
            return self._symbols[key]
        if self.parent is not None:
            return self.parent.resolve(name)
        return None

    def all_symbols(self) -> List[Symbol]:
        return list(self._symbols.values())

    def __contains__(self, name: str) -> bool:
        return self.resolve(name) is not None

    def __len__(self) -> int:
        return len(self._symbols)


class SymbolTable:
    """Collection-level symbol table for a single query.

    Tracks table aliases (which map to collection schemas) as distinct from the
    field scope, so qualified references like ``u.name`` can be resolved while
    unqualified references can be checked for ambiguity.
    """

    def __init__(self) -> None:
        #: alias (lowercased) -> (collection schema, original alias)
        self.aliases: Dict[str, CollectionSchema] = {}
        #: alias -> list of column names that alias exposes (for ``alias.*``).
        self.alias_fields: Dict[str, List[str]] = {}
        self.scope = Scope("query")
        #: SELECT-list aliases (name -> Symbol), usable in ORDER BY/HAVING.
        self.select_aliases: Dict[str, Symbol] = {}

    def bind_collection(
        self, alias: str, schema: CollectionSchema, fields: List[str]
    ) -> None:
        key = alias.lower()
        self.aliases[key] = schema
        self.alias_fields[key] = fields
        self.scope.define(
            Symbol(name=alias, kind="collection", source=schema.name)
        )

    def resolve_alias(self, alias: str) -> Optional[CollectionSchema]:
        return self.aliases.get(alias.lower())

    def has_alias(self, alias: str) -> bool:
        return alias.lower() in self.aliases

    def alias_count(self) -> int:
        return len(self.aliases)

    def define_alias_symbol(self, symbol: Symbol) -> None:
        self.select_aliases[symbol.name.lower()] = symbol

    def resolve_select_alias(self, name: str) -> Optional[Symbol]:
        return self.select_aliases.get(name.lower())

    @property
    def collections(self) -> Iterable[CollectionSchema]:
        return self.aliases.values()
