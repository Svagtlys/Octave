"""Model-binding resolution — the shared seam between planes (issue #36).

Extracted from ``octave.agent.instances.resolve_model`` and
``octave.context.archiver._resolve_binding``: the 2026-09-30 ADR prescribed
extraction on a third consumer (the context token budget is that consumer).
Pure: no I/O, no DB, no inference calls. Plane error types stay in their
planes — callers map :class:`BindingNotResolved` to their own
(``ModelBindingError`` / ``ModelBindingNotResolved``).
"""

from collections.abc import Callable
from dataclasses import dataclass

from octave.db.types import ExplicitModelBinding, ModelBinding

__all__ = ["BindingNotResolved", "ResolvedBinding", "resolve_binding"]


class BindingNotResolved(ValueError):
    """Tag binding without a lookup, or a tag that resolves to nothing."""


@dataclass(frozen=True)
class ResolvedBinding:
    """Concrete (adapter, model) pair. ``adapter=None`` means "use the
    configured default adapter" (tag bindings defer engine selection to
    the inference layer)."""

    adapter: str | None
    model: str


def resolve_binding(
    binding: ModelBinding,
    *,
    tag_lookup: Callable[[str], str | None] | None = None,
) -> ResolvedBinding:
    """Resolve a binding to a concrete pair. A tag miss fails loud — an
    agent with no usable model is unusable, and silent fallback would hide
    misconfiguration (agent design spec §4)."""
    if isinstance(binding, ExplicitModelBinding):
        return ResolvedBinding(adapter=binding.adapter, model=binding.model)
    if tag_lookup is None:
        raise BindingNotResolved(f"tag binding {binding.tag!r} requires a tag_lookup")
    model = tag_lookup(binding.tag)
    if model is None:
        raise BindingNotResolved(f"model tag {binding.tag!r} resolves to nothing")
    return ResolvedBinding(adapter=None, model=model)
