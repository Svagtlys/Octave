"""Callers must be able to catch every adapter failure as AdapterError."""

from octave.inference.errors import (
    AdapterAuthError,
    AdapterConnectionError,
    AdapterError,
    AdapterLoadError,
    AdapterRateLimitError,
    AdapterRegistrationError,
    AdapterResponseError,
    ModelNotFoundError,
    UnknownAdapterError,
)


def test_every_error_is_an_adapter_error() -> None:
    for cls in (
        AdapterConnectionError,
        AdapterAuthError,
        AdapterRateLimitError,
        ModelNotFoundError,
        AdapterResponseError,
        AdapterLoadError,
        AdapterRegistrationError,
        UnknownAdapterError,
    ):
        assert issubclass(cls, AdapterError)


def test_adapter_response_error_carries_status_code() -> None:
    error = AdapterResponseError("upstream 500", status_code=500)
    assert error.status_code == 500
    assert str(error) == "upstream 500"


def test_unknown_adapter_error_lists_known_names() -> None:
    error = UnknownAdapterError("anthropic", ["fake", "openai"])
    assert error.name == "anthropic"
    assert error.known == ["fake", "openai"]
    assert "anthropic" in str(error)
    assert "fake" in str(error) and "openai" in str(error)


def test_unknown_adapter_error_with_no_registered_names() -> None:
    error = UnknownAdapterError("openai", [])
    assert "no adapters registered" in str(error)
