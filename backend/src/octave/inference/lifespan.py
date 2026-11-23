"""App lifespan for the inference engine (issue #60).

Builds the configured adapter unconditionally from ``InferenceSettings``
and publishes it on ``app.state.inference_adapter`` (+ ``_name`` for the
binding-match check in ``DefaultTurnRunner``). Same boot posture as the
MCP fleet: an unreachable engine does not abort startup — completion
failures surface per-request as ``AdapterError``. Tests override the
state attributes with fakes (precedent: ``auth_hasher``).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from octave.inference.registry import default_registry
from octave.inference.config import InferenceSettings

__all__ = ["inference_lifespan"]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def inference_lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = InferenceSettings().to_adapter_config()
    adapter = default_registry.create(config)
    app.state.inference_adapter = adapter
    app.state.inference_adapter_name = config.adapter
    logger.info("inference adapter ready: %s", config.adapter)
    try:
        yield
    finally:
        await adapter.aclose()
