"""Shared pytest fixtures for OptiQuery tests."""
import pytest

from optisql.metadata import (
    Catalog,
    CollectionSchema,
    FieldDef,
    FieldType,
)


def build_sample_catalog(strict: bool = True) -> Catalog:
    """A small catalog with two collections used across tests."""
    catalog = Catalog(strict=strict)

    users = CollectionSchema(name="users", cardinality=10000, avg_doc_size=300)
    users.add_field(FieldDef("id", FieldType.INTEGER, indexed=True, nullable=False))
    users.add_field(FieldDef("name", FieldType.STRING))
    users.add_field(FieldDef("age", FieldType.INTEGER, indexed=True))
    users.add_field(FieldDef("country", FieldType.STRING, indexed=True))
    users.add_field(FieldDef("email", FieldType.STRING, nullable=True))
    catalog.add_collection(users)

    orders = CollectionSchema(name="orders", cardinality=50000, avg_doc_size=200)
    orders.add_field(FieldDef("id", FieldType.INTEGER, indexed=True))
    orders.add_field(FieldDef("user_id", FieldType.INTEGER, indexed=True))
    orders.add_field(FieldDef("total", FieldType.DECIMAL))
    orders.add_field(FieldDef("status", FieldType.STRING))
    catalog.add_collection(orders)

    return catalog


@pytest.fixture
def sample_catalog():
    return build_sample_catalog()


@pytest.fixture
def permissive_catalog():
    return Catalog(strict=False)
