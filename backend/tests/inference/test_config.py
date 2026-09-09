"""Config: frozen AdapterConfig for the registry; env-backed InferenceSettings."""

import dataclasses

import pytest

from octave.inference.config import AdapterConfig, InferenceSettings

_ENV_VARS = [
    "OCTAVE_INFERENCE_ADAPTER",
    "OCTAVE_INFERENCE_BASE_URL",
    "OCTAVE_INFERENCE_API_KEY",
    "OCTAVE_INFERENCE_DEFAULT_MODEL",
    "OCTAVE_INFERENCE_TIMEOUT_SECONDS",
    "OCTAVE_INFERENCE_MAX_RETRIES",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from the developer's real environment (tests run independently)."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_adapter_config_defaults() -> None:
    config = AdapterConfig(adapter="openai", base_url="http://localhost:11434/v1")
    assert config.api_key == ""
    assert config.default_model is None
    assert config.timeout_seconds == 120.0
    assert config.max_retries == 2
    assert config.extra == {}


def test_adapter_config_extra_not_shared() -> None:
    a = AdapterConfig(adapter="a", base_url="http://x/v1")
    b = AdapterConfig(adapter="b", base_url="http://x/v1")
    a.extra["num_ctx"] = 8192
    assert b.extra == {}


def test_adapter_config_is_frozen() -> None:
    config = AdapterConfig(adapter="openai", base_url="http://x/v1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.base_url = "http://other/v1"  # type: ignore[misc]


def test_settings_defaults_without_env() -> None:
    config = InferenceSettings().to_adapter_config()
    assert config.adapter == "openai"
    assert config.base_url == "http://localhost:11434/v1"  # Ollama default
    assert config.timeout_seconds == 120.0


def test_settings_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_INFERENCE_ADAPTER", "my_pkg.adapters:MyAdapter")
    monkeypatch.setenv("OCTAVE_INFERENCE_BASE_URL", "http://vllm.test:8000/v1")
    monkeypatch.setenv("OCTAVE_INFERENCE_API_KEY", "secret")
    monkeypatch.setenv("OCTAVE_INFERENCE_DEFAULT_MODEL", "qwen2.5-coder")
    monkeypatch.setenv("OCTAVE_INFERENCE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("OCTAVE_INFERENCE_MAX_RETRIES", "0")
    config = InferenceSettings().to_adapter_config()
    assert config.adapter == "my_pkg.adapters:MyAdapter"
    assert config.base_url == "http://vllm.test:8000/v1"
    assert config.api_key == "secret"
    assert config.default_model == "qwen2.5-coder"
    assert config.timeout_seconds == 30.0
    assert config.max_retries == 0
