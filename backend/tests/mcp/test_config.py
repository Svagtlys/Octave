"""Frozen server configs + env-backed global defaults."""

from dataclasses import FrozenInstanceError

import pytest

from octave.mcp.config import HttpConfig, McpSettings, StdioConfig


def test_stdio_config_defaults() -> None:
    config = StdioConfig(command="mcp-server")
    assert config.args == []
    assert config.env == {}


def test_configs_are_frozen() -> None:
    config = StdioConfig(command="mcp-server")
    with pytest.raises(FrozenInstanceError):
        config.command = "other"  # type: ignore[misc]


def test_stdio_repr_redacts_env_values() -> None:
    config = StdioConfig(command="srv", env={"API_KEY": "hunter2"})
    text = repr(config)
    assert "hunter2" not in text
    assert "***" in text


def test_http_repr_redacts_header_values() -> None:
    config = HttpConfig(
        url="https://x.example/mcp", headers={"Authorization": "Bearer t0ken"}
    )
    text = repr(config)
    assert "t0ken" not in text
    assert "Bearer" not in text
    assert "***" in text


def test_settings_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTAVE_MCP_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert McpSettings().request_timeout_seconds == 30.0


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_MCP_REQUEST_TIMEOUT_SECONDS", "5")
    assert McpSettings().request_timeout_seconds == 5.0


def test_settings_default_initialize_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS", raising=False)
    assert McpSettings().initialize_timeout_seconds == 30.0


def test_settings_env_override_initialize_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS", "8")
    assert McpSettings().initialize_timeout_seconds == 8.0


class TestSupervisionSettings:
    """Lifecycle-manager knobs (spec: restart policy table)."""

    def test_defaults(self) -> None:
        settings = McpSettings()
        assert settings.restart_base_delay_seconds == 1.0
        assert settings.restart_max_delay_seconds == 60.0
        assert settings.restart_max_attempts == 5
        assert settings.probe_timeout_seconds == 5.0
        assert settings.stabilization_seconds == 60.0
