"""Schema and collection metadata used by semantic analysis and the cost model."""
from .schema import (
    FieldType,
    FieldDef,
    CollectionSchema,
    Catalog,
    AGGREGATE_FUNCTIONS,
    SCALAR_FUNCTIONS,
    AGGREGATE_RESULT_TYPE,
    CatalogProvider,
    InMemoryCatalog,
)

__all__ = [
    "FieldType",
    "FieldDef",
    "CollectionSchema",
    "Catalog",
    "AGGREGATE_FUNCTIONS",
    "SCALAR_FUNCTIONS",
    "AGGREGATE_RESULT_TYPE",
    "CatalogProvider",
    "InMemoryCatalog",
]
