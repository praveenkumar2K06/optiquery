"""Backend interface definitions, adapters, and a name-based registry."""
from .base import (
    Backend,
    BackendCapability,
    BackendOperation,
    NODE_CAPABILITY,
)
from .mongodb import MongoDBBackend
from .redis import RedisBackend
from .registry import (
    available_backends,
    get_backend,
    has_backend,
    register_backend,
)

__all__ = [
    "Backend",
    "BackendCapability",
    "BackendOperation",
    "NODE_CAPABILITY",
    "MongoDBBackend",
    "RedisBackend",
    "available_backends",
    "get_backend",
    "has_backend",
    "register_backend",
]
