"""Backend registry: resolve a backend name to an adapter instance."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Backend
from .mongodb import MongoDBBackend
from .redis import RedisBackend

#: Registered backend classes, keyed by their ``name``.
_BACKENDS: Dict[str, type] = {
    MongoDBBackend.name: MongoDBBackend,
    RedisBackend.name: RedisBackend,
}


def available_backends() -> List[str]:
    """Return the names of all registered backends."""
    return sorted(_BACKENDS)


def register_backend(cls: type) -> type:
    """Register a backend class so it can be resolved by name."""
    if not issubclass(cls, Backend):  # pragma: no cover - misuse guard
        raise TypeError("register_backend expects a Backend subclass")
    _BACKENDS[cls.name] = cls
    return cls


def get_backend(name: str, **kwargs: Any) -> Backend:
    """Instantiate a backend by name.

    Raises:
        ValueError: if the backend name is unknown.
    """
    key = name.lower()
    if key not in _BACKENDS:
        raise ValueError(
            f"Unknown backend '{name}'. Available backends: "
            f"{', '.join(available_backends())}"
        )
    return _BACKENDS[key](**kwargs)


def has_backend(name: str) -> bool:
    return name.lower() in _BACKENDS


__all__ = [
    "available_backends",
    "register_backend",
    "get_backend",
    "has_backend",
]
