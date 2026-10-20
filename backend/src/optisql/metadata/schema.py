"""Schema metadata: field types, collections, and a schema catalog.

The catalog is deliberately backend-agnostic. A compiler client can register
schemas up-front (from a schema file, from MongoDB collection sampling, or from
another data source). When a collection is unknown, semantic analysis applies a
documented permissive policy rather than pretending the field was validated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class FieldType(str, Enum):
    """Logical field types understood by the compiler."""

    INTEGER = "integer"
    DECIMAL = "decimal"
    STRING = "string"
    BOOLEAN = "boolean"
    DATE = "date"
    NULL = "null"
    ANY = "any"
    ARRAY = "array"
    OBJECT = "object"

    @property
    def is_numeric(self) -> bool:
        return self in (FieldType.INTEGER, FieldType.DECIMAL)

    @property
    def is_comparable(self) -> bool:
        return self in (
            FieldType.INTEGER,
            FieldType.DECIMAL,
            FieldType.STRING,
            FieldType.DATE,
            FieldType.BOOLEAN,
        )


#: Functions that are recognized as aggregates.
AGGREGATE_FUNCTIONS = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})

#: Scalar functions recognized by the compiler.
SCALAR_FUNCTIONS = frozenset({"ABS", "LOWER", "UPPER", "LENGTH", "ROUND", "COALESCE"})

#: Mapping from a function name to its expected result type.
AGGREGATE_RESULT_TYPE = {
    "COUNT": FieldType.INTEGER,
    "SUM": FieldType.ANY,
    "AVG": FieldType.DECIMAL,
    "MIN": FieldType.ANY,
    "MAX": FieldType.ANY,
}


@dataclass
class FieldDef:
    """Definition of a single field/column."""

    name: str
    type: FieldType = FieldType.ANY
    nullable: bool = True
    indexed: bool = False
    #: Optional average byte size used by the cost model.
    avg_size: Optional[int] = None
    #: Optional number of distinct values (for selectivity estimation).
    distinct_values: Optional[int] = None


@dataclass
class CollectionSchema:
    """A collection/table with its fields and optional statistics."""

    name: str
    fields: Dict[str, FieldDef] = field(default_factory=dict)
    #: Estimated number of documents/rows. ``None`` means unknown.
    cardinality: Optional[int] = None
    #: Average document size in bytes.
    avg_doc_size: Optional[int] = None

    def get_field(self, name: str) -> Optional[FieldDef]:
        return self.fields.get(name)

    def has_field(self, name: str) -> bool:
        return name in self.fields

    @property
    def indexed_fields(self) -> List[str]:
        return [f.name for f in self.fields.values() if f.indexed]

    def add_field(self, field_def: FieldDef) -> None:
        self.fields[field_def.name] = field_def


@dataclass
class Catalog:
    """A registry of known collection schemas."""

    collections: Dict[str, CollectionSchema] = field(default_factory=dict)
    #: Whether a reference to an unknown collection is an error or a warning.
    strict: bool = True

    def has_collection(self, name: str) -> bool:
        return name.lower() in self.collections

    def get_collection(self, name: str) -> Optional[CollectionSchema]:
        return self.collections.get(name.lower())

    def add_collection(self, schema: CollectionSchema) -> CollectionSchema:
        self.collections[schema.name.lower()] = schema
        return schema

    def add_field(self, collection: str, field_def: FieldDef) -> None:
        schema = self.collections.get(collection.lower())
        if schema is None:
            schema = self.add_collection(CollectionSchema(name=collection))
        schema.add_field(field_def)


class CatalogProvider:
    """Interface for objects that can supply a :class:`Catalog`."""

    def get_catalog(self) -> Catalog:  # pragma: no cover - interface
        raise NotImplementedError


class InMemoryCatalog(CatalogProvider):
    """A catalog provider backed by an in-memory :class:`Catalog`."""

    def __init__(self, catalog: Optional[Catalog] = None):
        self._catalog = catalog if catalog is not None else Catalog()

    def get_catalog(self) -> Catalog:
        return self._catalog

    @classmethod
    def from_dict(cls, data: dict, strict: bool = True) -> "InMemoryCatalog":
        """Build a catalog from a plain dict description.

        ``data`` maps collection names to ``{"fields": {...}, "cardinality": N,
        "avg_doc_size": N}``. Field entries map names to ``{"type": "integer",
        "indexed": true, ...}``.
        """
        catalog = Catalog(strict=strict)
        for coll_name, coll_data in data.items():
            schema = CollectionSchema(
                name=coll_name,
                cardinality=coll_data.get("cardinality"),
                avg_doc_size=coll_data.get("avg_doc_size"),
            )
            for fname, fdata in coll_data.get("fields", {}).items():
                schema.add_field(
                    FieldDef(
                        name=fname,
                        type=FieldType(fdata.get("type", "any")),
                        nullable=fdata.get("nullable", True),
                        indexed=fdata.get("indexed", False),
                        avg_size=fdata.get("avg_size"),
                        distinct_values=fdata.get("distinct_values"),
                    )
                )
            catalog.add_collection(schema)
        return cls(catalog)
