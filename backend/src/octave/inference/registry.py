"""Adapter registry: adapter name or import string → adapter instance."""

import importlib
import logging
from collections.abc import Callable

from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.errors import (
    AdapterLoadError,
    AdapterRegistrationError,
    UnknownAdapterError,
)

__all__ = ["AdapterRegistry", "default_registry", "register"]

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Maps adapter names (or ``module.path:ClassName`` strings) to classes."""

    def __init__(self) -> None:
        self._adapters: dict[str, type[InferenceAdapter]] = {}

    def register(self, name: str, cls: type[InferenceAdapter]) -> None:
        """Register a class under a name. Duplicates fail fast."""
        if name in self._adapters:
            raise AdapterRegistrationError(f"Adapter {name!r} is already registered")
        self._adapters[name] = cls

    def names(self) -> list[str]:
        """Registered adapter names, sorted (for diagnostics / Settings UI)."""
        return sorted(self._adapters)

    def resolve(self, name_or_import_string: str) -> type[InferenceAdapter]:
        """Resolve a registered name, else a ``module.path:ClassName`` plugin."""
        if name_or_import_string in self._adapters:
            return self._adapters[name_or_import_string]
        if ":" in name_or_import_string:
            return self._resolve_import_string(name_or_import_string)
        logger.warning(
            "Unknown adapter %r; known adapters: %s",
            name_or_import_string,
            self.names(),
        )
        raise UnknownAdapterError(name_or_import_string, self._adapters.keys())

    def create(self, config: AdapterConfig) -> InferenceAdapter:
        """Instantiate the adapter named in the config."""
        return self.resolve(config.adapter)(config)

    def _resolve_import_string(self, import_string: str) -> type[InferenceAdapter]:
        module_name, _, class_name = import_string.partition(":")
        try:
            cls: object = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError) as exc:
            logger.warning("Failed to load adapter %r: %s", import_string, exc)
            raise AdapterLoadError(
                f"Could not load adapter {import_string!r}"
            ) from exc
        if not (isinstance(cls, type) and issubclass(cls, InferenceAdapter)):
            logger.warning("%r is not an InferenceAdapter subclass", import_string)
            raise AdapterLoadError(
                f"{import_string!r} does not resolve to an InferenceAdapter subclass"
            )
        return cls


default_registry = AdapterRegistry()


def register(
    name: str, *, registry: AdapterRegistry | None = None
) -> Callable[[type[InferenceAdapter]], type[InferenceAdapter]]:
    """Class decorator registering the adapter; defaults to the global registry."""
    target = default_registry if registry is None else registry

    def decorator(cls: type[InferenceAdapter]) -> type[InferenceAdapter]:
        target.register(name, cls)
        return cls

    return decorator
