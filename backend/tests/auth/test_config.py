"""AuthSettings env wiring (OCTAVE_AUTH_*)."""

import pytest

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


def test_settings_slide_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTAVE_AUTH_TOUCH_GRACE_SECONDS", raising=False)
    monkeypatch.delenv("OCTAVE_AUTH_TOUCH_TIMEOUT_SECONDS", raising=False)
    settings = AuthSettings()
    assert settings.touch_grace_seconds == 60
    assert settings.touch_timeout_seconds == 2.0


def test_settings_slide_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_AUTH_TOUCH_GRACE_SECONDS", "120")
    monkeypatch.setenv("OCTAVE_AUTH_TOUCH_TIMEOUT_SECONDS", "0.5")
    settings = AuthSettings()
    assert settings.touch_grace_seconds == 120
    assert settings.touch_timeout_seconds == 0.5
