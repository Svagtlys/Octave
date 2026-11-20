"""Token-budget arithmetic (issue #36). Pure module: no DB, no network."""

import math
from dataclasses import dataclass

import pytest

from octave.context.budget import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_CONTEXT_WINDOW_FRACTION,
    bundle_tokens,
    estimate_tokens,
    resolve_budget,
)


@dataclass(frozen=True)
class _Item:
    content: str


def test_estimate_tokens_boundaries() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("x") == 1
    assert estimate_tokens("xxxx") == 1
    assert estimate_tokens("xxxxx") == 2
    assert estimate_tokens("x" * 400) == 100


def test_bundle_tokens_sums_items() -> None:
    items = [_Item(content="x" * 10), _Item(content="y" * 5)]
    assert bundle_tokens(items) == estimate_tokens("x" * 10) + estimate_tokens("y" * 5)
    assert bundle_tokens([]) == 0


def test_resolve_budget_ceiling_only() -> None:
    assert resolve_budget(model_window=8192, per_agent_budget=None) == 2048


def test_resolve_budget_per_agent_wins_when_smaller() -> None:
    assert resolve_budget(model_window=8192, per_agent_budget=500) == 500


def test_resolve_budget_ceiling_wins_when_per_agent_larger() -> None:
    assert resolve_budget(model_window=8192, per_agent_budget=100_000) == 2048


def test_resolve_budget_custom_fraction() -> None:
    assert resolve_budget(model_window=1000, per_agent_budget=None, fraction=0.5) == 500


def test_resolve_budget_floors_fraction_result() -> None:
    # 1001 * 0.25 = 250.25 -> 250
    assert resolve_budget(model_window=1001, per_agent_budget=None) == 250


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5])
def test_resolve_budget_rejects_bad_fraction(fraction: float) -> None:
    with pytest.raises(ValueError):
        resolve_budget(model_window=4096, per_agent_budget=None, fraction=fraction)


@pytest.mark.parametrize("window", [0, -1])
def test_resolve_budget_rejects_bad_window(window: int) -> None:
    with pytest.raises(ValueError):
        resolve_budget(model_window=window, per_agent_budget=None)


def test_defaults_match_spec() -> None:
    assert DEFAULT_CONTEXT_WINDOW_FRACTION == 0.25
    assert DEFAULT_CONTEXT_WINDOW == 8192
    assert math.ceil(4 / 4) == 1  # sanity on the ceil convention
