"""Auth plane (issue #122): who is making this request.

Self-contained package mirroring ``octave.inference`` / ``octave.mcp``.
Public surface: hasher + token primitives, :class:`AuthStore`,
:class:`AuthService`, dependencies, settings, and the error hierarchy.

The two seams every login method funnels through — ``provision_user`` and
``issue_session`` — live on ``AuthService``; adding a login method (OIDC)
never touches deps, routes, or the WS layer.
"""

from octave.auth.config import AuthSettings
from octave.auth.errors import (
    AccountDisabled,
    AlreadyBootstrapped,
    AuthError,
    InvalidCredentials,
    NotLastOwnerGuard,
    SessionExpired,
    UsernameTaken,
    http_status_of,
)
from octave.auth.passwords import Argon2Hasher, DummyHasher, PasswordHasher
from octave.auth.tokens import generate_token, hash_token

__all__ = [
    "AccountDisabled",
    "AlreadyBootstrapped",
    "Argon2Hasher",
    "AuthError",
    "AuthSettings",
    "DummyHasher",
    "InvalidCredentials",
    "NotLastOwnerGuard",
    "PasswordHasher",
    "SessionExpired",
    "UsernameTaken",
    "generate_token",
    "hash_token",
    "http_status_of",
]
