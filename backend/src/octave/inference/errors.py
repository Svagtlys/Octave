"""Octave adapter exception hierarchy.

Wire-layer exceptions (the ``openai`` SDK and below) must never escape an
adapter; they are translated to these types at the adapter boundary.
"""

from collections.abc import Iterable

__all__ = [
    "AdapterAuthError",
    "AdapterConnectionError",
    "AdapterError",
    "AdapterLoadError",
    "AdapterRateLimitError",
    "AdapterRegistrationError",
    "AdapterResponseError",
    "ModelNotFoundError",
    "UnknownAdapterError",
]


class AdapterError(Exception):
    """Base class for every inference adapter failure."""


class AdapterConnectionError(AdapterError):
    """The engine could not be reached (connection refused, DNS, timeout)."""


class AdapterAuthError(AdapterError):
    """The engine rejected our credentials."""


class AdapterRateLimitError(AdapterError):
    """The engine rate-limited us."""


class ModelNotFoundError(AdapterError):
    """The requested model does not exist on the engine."""


class AdapterResponseError(AdapterError):
    """The engine returned an unexpected HTTP status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdapterLoadError(AdapterError):
    """An adapter could not be loaded (bad import string, not a subclass)."""


class AdapterRegistrationError(AdapterError):
    """An adapter name was registered twice (fail fast at import time)."""


class UnknownAdapterError(AdapterError):
    """No adapter matches the requested name."""

    def __init__(self, name: str, known: Iterable[str]) -> None:
        self.name = name
        self.known = sorted(known)
        listed = ", ".join(self.known) if self.known else "no adapters registered"
        super().__init__(f"Unknown adapter {name!r}. Known adapters: {listed}")
