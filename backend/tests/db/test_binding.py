"""Shared model-binding resolution (issue #36 extraction). Pure: no I/O."""

import pytest

from octave.db.binding import BindingNotResolved, ResolvedBinding, resolve_binding
from octave.db.types import ExplicitModelBinding, TagModelBinding


def test_resolve_explicit_binding() -> None:
    resolved = resolve_binding(
        ExplicitModelBinding(kind="explicit", adapter="openai", model="llama3")
    )
    assert resolved == ResolvedBinding(adapter="openai", model="llama3")


def test_resolve_tag_binding_with_lookup() -> None:
    resolved = resolve_binding(
        TagModelBinding(kind="tag", tag="quick"), tag_lookup=lambda tag: "llama3"
    )
    assert resolved == ResolvedBinding(adapter=None, model="llama3")


def test_tag_binding_requires_lookup() -> None:
    with pytest.raises(BindingNotResolved):
        resolve_binding(TagModelBinding(kind="tag", tag="quick"))


def test_tag_miss_fails_loud() -> None:
    with pytest.raises(BindingNotResolved):
        resolve_binding(
            TagModelBinding(kind="tag", tag="nope"), tag_lookup=lambda t: None
        )
