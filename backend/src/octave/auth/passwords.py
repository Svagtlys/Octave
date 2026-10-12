"""Password hashing seam.

``PasswordHasher`` is a Protocol so tests inject a fast implementation —
argon2id is deliberately ~100 ms per hash (that's the point) and would drag
the whole suite. Production wires :class:`Argon2Hasher`.
"""

from typing import Protocol

from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2.exceptions import (
    InvalidHashError as _InvalidHashError,
)
from argon2.exceptions import (
    VerificationError as _VerificationError,
)
from argon2.exceptions import (
    VerifyMismatchError as _VerifyMismatchError,
)

__all__ = ["Argon2Hasher", "DummyHasher", "PasswordHasher"]

_DUMMY_PREFIX = "dummy$"


class PasswordHasher(Protocol):
    """hash / verify / needs_rehash — the argon2-cffi shape, implementable
    cheaply for tests."""

    def hash(self, password: str) -> str: ...

    def verify(self, encoded: str, password: str) -> bool: ...

    def needs_rehash(self, encoded: str) -> bool: ...


class Argon2Hasher:
    """argon2id defaults per OWASP parameters (argon2-cffi defaults)."""

    def __init__(self) -> None:
        self._hasher = _Argon2PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        """False on any verification failure, including garbage/foreign hash
        formats — callers must never see an exception leak."""
        try:
            return self._hasher.verify(encoded, password)
        except (_VerifyMismatchError, _VerificationError, _InvalidHashError):
            return False

    def needs_rehash(self, encoded: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(encoded)
        except (_VerificationError, _InvalidHashError):
            return False


class DummyHasher:
    """Fast, deterministic stand-in for tests: ``dummy$<password>``.

    NOT secure — never wire this into a running server. Also used to burn a
    verify on unknown usernames so login timing doesn't leak existence.
    """

    def hash(self, password: str) -> str:
        return f"{_DUMMY_PREFIX}{password}"

    def verify(self, encoded: str, password: str) -> bool:
        return encoded == self.hash(password)

    def needs_rehash(self, encoded: str) -> bool:
        return False
