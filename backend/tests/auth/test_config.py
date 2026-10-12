"""AuthSettings env wiring (OCTAVE_AUTH_*)."""

from octave.auth.config import AuthSettings


def test_defaults() -> None:
    settings = AuthSettings()
    assert settings.cookie_secure is False
    assert settings.idle_ttl_days == 14
    assert settings.absolute_ttl_days == 90


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("OCTAVE_AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("OCTAVE_AUTH_IDLE_TTL_DAYS", "7")
    monkeypatch.setenv("OCTAVE_AUTH_ABSOLUTE_TTL_DAYS", "30")
    settings = AuthSettings()
    assert settings.cookie_secure is True
    assert settings.idle_ttl_days == 7
    assert settings.absolute_ttl_days == 30
