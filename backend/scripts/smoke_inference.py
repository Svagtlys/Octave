#!/usr/bin/env python
"""Manual smoke test: point the inference layer at a live engine.

Usage (from backend/):

    OCTAVE_INFERENCE_BASE_URL=http://localhost:11434/v1 \
    OCTAVE_INFERENCE_DEFAULT_MODEL=qwen2.5-coder \
    uv run scripts/smoke_inference.py "Hello, who are you?"

Reads the same OCTAVE_INFERENCE_* env vars the app uses (via
InferenceSettings), lists models, sends the prompt, prints the reply.
Exits non-zero on any AdapterError. Not part of the test suite.

HTTPS engines behind an internal CA may fail with a bare
"AdapterConnectionError: Connection error." while curl succeeds: httpx
trusts certifi's bundle, not the system store. Point it at the system
bundle to fix: SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
"""

import argparse
import asyncio
import sys

from octave.inference import (
    AdapterError,
    CompletionRequest,
    InferenceSettings,
    Message,
    default_registry,
)


async def run(prompt: str, *, stream: bool) -> int:
    """Create the configured adapter, probe it, and answer the prompt."""
    settings = InferenceSettings()
    config = settings.to_adapter_config()
    print(f"adapter:  {config.adapter}")
    print(f"base_url: {config.base_url}")

    try:
        adapter = default_registry.create(config)
    except AdapterError as exc:
        print(f"\u2717 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        models = await adapter.list_models()
        print(f"models:   {', '.join(m.id for m in models) or '(none)'}")
        model = config.default_model or (models[0].id if models else None)
        if model is None:
            print(
                "\u2717 engine serves no models and "
                "OCTAVE_INFERENCE_DEFAULT_MODEL is unset",
                file=sys.stderr,
            )
            return 1

        request = CompletionRequest(
            model=model,
            messages=[Message(role="user", content=prompt)],
        )
        print(f"\n\u2192 {prompt}\n")
        if stream:
            async for chunk in adapter.stream(request):
                print(chunk.delta_text, end="", flush=True)
            print("\n\n\u2713 stream finished")
        else:
            result = await adapter.complete(request)
            print(result.text)
            print(
                f"\n\u2713 finish_reason={result.finish_reason!r} "
                f"model={result.model!r}"
            )
            return 0
    except AdapterError as exc:
        print(f"\n\u2717 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the inference adapter layer against a live engine."
    )
    parser.add_argument("prompt", nargs="+", help="The question to ask the model")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream deltas instead of one full completion",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(" ".join(args.prompt), stream=args.stream)))


if __name__ == "__main__":
    main()
