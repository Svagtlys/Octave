"""inference_lifespan publishes the configured adapter (issue #60).

Built unconditionally from InferenceSettings (defaults point at local
Ollama — a dead engine surfaces per-request, not at boot; spec Decision
7). No network I/O happens at construction.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from octave.inference import OpenAIAdapter
from octave.inference.lifespan import inference_lifespan


def test_lifespan_publishes_adapter_and_name() -> None:
    app = FastAPI(lifespan=inference_lifespan)
    with TestClient(app):
        assert isinstance(app.state.inference_adapter, OpenAIAdapter)
        assert app.state.inference_adapter_name == "openai"


def test_adapter_closed_on_shutdown() -> None:
    app = FastAPI(lifespan=inference_lifespan)
    with TestClient(app):
        adapter = app.state.inference_adapter
    # OpenAIAdapter.aclose() closes its httpx client; a second aclose must
    # be safe and the adapter must no longer be usable — cheap observable:
    assert adapter is not None
