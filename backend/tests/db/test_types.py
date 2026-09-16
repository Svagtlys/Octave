"""Octave DB domain types — event kinds, vector hits, payload models."""

import pytest
from pydantic import ValidationError

from octave.db.types import (
    AssistantMessagePayload,
    EventKind,
    UserMessagePayload,
    VectorHit,
)


def test_event_kind_wire_values() -> None:
    assert EventKind.USER_MESSAGE == "user_message"
    assert EventKind.ASSISTANT_MESSAGE == "assistant_message"
    assert EventKind.TOOL_CALL == "tool_call"
    assert EventKind.TOOL_RESULT == "tool_result"
    assert EventKind.SYSTEM == "system"


def test_event_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        EventKind("agent_run")


def test_event_kind_revalidates_stored_text() -> None:
    """``events.kind`` is TEXT in the DB; the enum is the app-level guard."""
    assert EventKind(EventKind.USER_MESSAGE.value) is EventKind.USER_MESSAGE


def test_vector_hit_carries_id_and_distance() -> None:
    hit = VectorHit(item_id="v_1", distance=0.125)
    assert hit.item_id == "v_1"
    assert hit.distance == 0.125


def test_message_payloads_require_content() -> None:
    assert UserMessagePayload(content="hi").content == "hi"
    assert AssistantMessagePayload(content="yo").content == "yo"
    with pytest.raises(ValidationError):
        UserMessagePayload()  # type: ignore[call-arg]


def test_assistant_payload_model_is_optional() -> None:
    assert AssistantMessagePayload(content="x").model_name is None
