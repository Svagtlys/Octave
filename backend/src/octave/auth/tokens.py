"""Session token primitives.

Raw tokens exist only inside ``Set-Cookie`` responses; the DB stores the
sha256 hex digest so a DB leak does not yield usable session tokens.
"""

import hashlib
import secrets

__all__ = ["generate_token", "hash_token"]


def generate_token() -> str:
    """32 urlsafe random bytes (~43 chars)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """sha256 hex digest — deterministic, 64 chars."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
