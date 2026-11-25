"""Auth configuration (``OCTAVE_AUTH_*`` env vars / ``.env``).

Mirrors ``octave.inference.config`` / ``octave.db.config``: pydantic-settings
bootstrap class. Cookie lifetimes are TTL policy, not secrets.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["AuthSettings"]


class AuthSettings(BaseSettings):
    """Env-backed auth policy (``OCTAVE_AUTH_*`` vars / ``.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="OCTAVE_AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cookie_secure: bool = False
    """Emit ``Secure`` on the session cookie. LAN deployments terminate TLS
    at a proxy and keep this False; flip when serving over HTTPS."""

    idle_ttl_days: int = 14
    """Sliding expiry: ``expires_at`` extends to now + idle_ttl_days on each
    authenticated use."""

    absolute_ttl_days: int = 90
    """Hard cap: sliding renewal never extends a session past
    ``created_at + absolute_ttl_days``."""

    touch_grace_seconds: int = 60
    """Throttle for the out-of-band session slide (issue #161): an authed
    request only rewrites ``last_seen_at``/``expires_at`` when the stored
    value is older than this. Expiry is advisory-precision by design."""

    touch_timeout_seconds: float = 2.0
    """Max time the request path waits for the out-of-band slide. Must stay
    below the SQLite ``busy_timeout`` (5 s) so a held write lock cancels the
    slide instead of delaying the response."""
