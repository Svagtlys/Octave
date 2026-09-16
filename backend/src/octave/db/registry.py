"""Adapter registry: adapter name or import string → adapter instance.

Mirrors ``octave.inference.registry`` exactly, so the Settings UI can list
registered adapters the same way it lists inference engines.
"""

import importlib
import logging
from collections.abc import Callable

from octave.db.adapter import DbAdapter
from octave.db.config import DbConfig
from octave.db.errors import (
    DbAdapterLoadError,
    DbAdapterRegistrationError,
    UnknownDbAdapterError,
)

__all__ = ["DbAdapterRegistry", "default_registry", "register_db"]

logger = logging.getLogger(__name__)


class DbAdapterRegistry:
    """Maps adapter names (or ``module.path:ClassName`` strings) to classes."""

    def __init__(self) -> None:
        self._adapters: dict[str, type[DbAdapter]] = {}

    def register(self, name: str, cls: type[DbAdapter]) -> None:
        """Register a class under a name. Duplicates fail fast."""
        if name in self._adapters:
            raise DbAdapterRegistrationError(f"Adapter {name!r} is already registered")
        self._adapters[name] = cls

    def names(self) -> list[str]:
        """Registered adapter names, sorted (for diagnostics / Settings UI)."""
        return sorted(self._adapters)

    def resolve(self, name_or_import_string: str) -> type[DbAdapter]:
        """Resolve a registered name, else a ``module.path:ClassName`` plugin."""
        if name_or_import_string in self._adapters:
            return self._adapters[name_or_import_string]
        if ":" in name_or_import_string:
            return self._resolve_import_string(name_or_import_string)
        logger.warning(
            "Unknown db adapter %r; known adapters: %s",
            name_or_import_string,
            self.names(),
        )
        raise UnknownDbAdapterError(name_or_import_string, self._adapters.keys())

    def create(self, config: DbConfig) -> DbAdapter:
        """Instantiate the adapter named in the config."""
        return self.resolve(config.adapter)(config)

    def _resolve_import_string(self, import_string: str) -> type[DbAdapter]:
        module_name, _, class_name = import_string.partition(":")
        try:
            cls: object = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError) as exc:
            logger.warning("Failed to load db adapter %r: %s", import_string, exc)
            raise DbAdapterLoadError(
                f"Could not load db adapter {import_string!r}"
            ) from exc
        if not (isinstance(cls, type) and issubclass(cls, DbAdapter)):
            logger.warning("%r is not a DbAdapter subclass", import_string)
            raise DbAdapterLoadError(
                f"{import_string!r} does not resolve to a DbAdapter subclass"
            )
        return cls


default_registry = DbAdapterRegistry()


def register_db(
    name: str, *, registry: DbAdapterRegistry | None = None
) -> Callable[[type[DbAdapter]], type[DbAdapter]]:
    """Class decorator registering the adapter; defaults to the global registry."""
    target = default_registry if registry is None else registry

    def decorator(cls: type[DbAdapter]) -> type[DbAdapter]:
        target.register(name, cls)
        return cls

    return decorator
