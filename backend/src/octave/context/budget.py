"""Token-budget arithmetic for context injection (issue #36).

Pure module: no DB, no network, no vault tolerance rules — the injector
sanitizes stored values before calling :func:`resolve_budget`. Counting is
the conservative char estimate ``ceil(chars / 4)``, mirroring the archiver's
``chars ≈ tokens × 3–4`` precedent (design Decision 4): deterministic, zero
deps, and over-estimation is visible because over-budget fails loud.
"""

from collections.abc import Sequence
from typing import Protocol

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_CONTEXT_WINDOW_FRACTION",
    "bundle_tokens",
    "estimate_tokens",
    "resolve_budget",
]

#: Conservative default for common local chat endpoints.
DEFAULT_CONTEXT_WINDOW = 8192

#: Share of the model window reserved for standing context (Decision 1).
DEFAULT_CONTEXT_WINDOW_FRACTION = 0.25

CHARS_PER_TOKEN = 4


class _HasContent(Protocol):
    @property
    def content(self) -> str: ...


def estimate_tokens(text: str) -> int:
    """Char estimate: ``ceil(len(text) / 4)``."""
    return -(-len(text) // CHARS_PER_TOKEN)  # ceil division


def bundle_tokens(items: Sequence[_HasContent]) -> int:
    """Sum of per-item content estimates."""
    return sum(estimate_tokens(item.content) for item in items)


def resolve_budget(
    *,
    model_window: int,
    per_agent_budget: int | None,
    fraction: float = DEFAULT_CONTEXT_WINDOW_FRACTION,
) -> int:
    """Effective budget: ``min(floor(window × fraction), per_agent)``;
    ``None`` per-agent → the ceiling alone. Programmer errors raise
    ``ValueError`` at wiring time (Decision 10)."""
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    if model_window < 1:
        raise ValueError("model_window must be >= 1")
    ceiling = int(model_window * fraction)
    if per_agent_budget is None:
        return ceiling
    return min(ceiling, per_agent_budget)
