"""Auth error hierarchy with central HTTP status mapping.

Handlers map these to JSON responses once, in ``octave.middleware`` — routes
raise domain errors, not HTTPExceptions, so the service layer stays
HTTP-agnostic and the WS layer can reuse it.
"""

__all__ = [
    "AccountDisabled",
    "AlreadyBootstrapped",
    "AuthError",
    "InvalidCredentials",
    "NotLastOwnerGuard",
    "SessionExpired",
    "UserNotFound",
    "UsernameTaken",
    "http_status_of",
]


class AuthError(Exception):
    """Base for auth-plane failures. Subclasses carry their HTTP mapping."""


class InvalidCredentials(AuthError):
    """Wrong username, wrong password, or a NULL-hash account. One error for
    all three — never reveal which half failed."""


class AccountDisabled(AuthError):
    """``status=deactivated``: login refused; existing sessions already gone."""


class SessionExpired(AuthError):
    """Token row exists but ``expires_at`` passed (idle or absolute)."""


class UsernameTaken(AuthError):
    """Registration collision on the unique username."""


class NotLastOwnerGuard(AuthError):
    """Refusing to demote/deactivate the only owner (lockout guard)."""


class AlreadyBootstrapped(AuthError):
    """First-run bootstrap ran once; the door is closed forever."""


class UserNotFound(AuthError):
    """Administration targeted an id that doesn't exist (404, not 401 —
    the caller is already authenticated)."""


def http_status_of(exc: AuthError) -> int:
    """Map an auth error to its HTTP status (401 auth failures, 404 admin
    misses, 409 policy conflicts). Single mapping point for middleware,
    routes and tests."""
    if isinstance(exc, (InvalidCredentials, AccountDisabled, SessionExpired)):
        return 401
    if isinstance(exc, UserNotFound):
        return 404
    return 409
