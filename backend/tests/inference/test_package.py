"""Public API surface and built-in self-registration."""

import octave.inference as inference


def test_builtin_openai_adapter_self_registers() -> None:
    assert "openai" in inference.default_registry.names()


def test_public_names_are_exported() -> None:
    for name in (
        "InferenceAdapter",
        "AdapterRegistry",
        "default_registry",
        "register",
        "AdapterConfig",
        "InferenceSettings",
        "OpenAIAdapter",
        "AdapterError",
        "CompletionRequest",
        "EmbeddingRequest",
        "ModelInfo",
    ):
        assert hasattr(inference, name), name
