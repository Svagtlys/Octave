"""Adapter configuration.

Two layers: ``AdapterConfig`` is what the registry and adapters consume —
populated from the environment today, from the unified DB at the
composition root later. ``api_key`` is a secret: never log it (redact ``***``).
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["AdapterConfig", "InferenceSettings"]


@dataclass(frozen=True)
class AdapterConfig:
    """Everything an adapter needs to connect. Frozen; safe to share."""

    adapter: str
    """Registered adapter name or a ``module.path:ClassName`` import string."""

    base_url: str
    api_key: str = ""
    default_model: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 2
    extra: dict[str, Any] = field(default_factory=dict)


class InferenceSettings(BaseSettings):
    """Env-backed bootstrap config (``OCTAVE_INFERENCE_*`` vars / ``.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="OCTAVE_INFERENCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    adapter: str = "openai"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    default_model: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 2

    def to_adapter_config(self) -> AdapterConfig:
        """Project these settings into the registry-facing config object."""
        return AdapterConfig(
            adapter=self.adapter,
            base_url=self.base_url,
            api_key=self.api_key,
            default_model=self.default_model,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
        )
