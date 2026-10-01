"""Octave DB domain types — event kinds, vector hits, payload models."""

import pytest
from pydantic import TypeAdapter, ValidationError

from octave.db.types import (
    AgentAssignments,
    AgentStatus,
    AssistantMessagePayload,
    ContextInjectionPayload,
    EventKind,
    InstanceStatus,
    ModelBinding,
    UserMessagePayload,
    VaultKind,
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


def test_vault_kind_wire_values() -> None:
    assert VaultKind.SKILL == "skill"
    assert VaultKind.PROMPT == "prompt"
    assert VaultKind.PREFERENCE == "preference"
    assert VaultKind.SESSION_SUMMARY == "session_summary"
    assert VaultKind.TRANSCRIPT_CHUNK == "transcript_chunk"


def test_vault_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        VaultKind("agent_state")


def test_vault_kind_revalidates_stored_text() -> None:
    """``vault_items.kind`` is TEXT in the DB; the enum is the app-level guard."""
    assert VaultKind(VaultKind.TRANSCRIPT_CHUNK.value) is VaultKind.TRANSCRIPT_CHUNK


def test_vault_kind_membership_is_exhaustive() -> None:
    assert {kind.value for kind in VaultKind} == {
        "skill",
        "prompt",
        "preference",
        "session_summary",
        "transcript_chunk",
    }


def test_agent_status_values() -> None:
    assert AgentStatus.ACTIVE == "active"
    assert AgentStatus.PAUSED == "paused"


def test_instance_status_values() -> None:
    assert InstanceStatus.IDLE == "idle"
    assert InstanceStatus.ACTIVE == "active"


def test_model_binding_tag_form_parses() -> None:
    binding = TypeAdapter(ModelBinding).validate_python({"kind": "tag", "tag": "quick"})
    assert binding.kind == "tag"
    assert binding.tag == "quick"


def test_model_binding_explicit_form_parses() -> None:
    binding = TypeAdapter(ModelBinding).validate_python(
        {"kind": "explicit", "adapter": "openai", "model": "llama3"}
    )
    assert binding.kind == "explicit"
    assert binding.adapter == "openai"
    assert binding.model == "llama3"


def test_model_binding_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ModelBinding).validate_python({"kind": "magic"})


def test_model_binding_explicit_requires_model() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ModelBinding).validate_python(
            {"kind": "explicit", "adapter": "openai"}
        )


def test_assignments_defaults_are_empty() -> None:
    assignments = AgentAssignments()
    assert assignments.prompt is None
    assert assignments.skills == []
    assert assignments.preference_tags == []


def test_assignments_allow_extra_keys() -> None:
    assignments = AgentAssignments.model_validate({"workflow": "vi_1"})
    assert assignments.workflow == "vi_1"


def test_event_kind_context_injection_value_stable() -> None:
    assert EventKind.CONTEXT_INJECTION == "context_injection"
    assert EventKind("context_injection") is EventKind.CONTEXT_INJECTION
    assert str(EventKind.CONTEXT_INJECTION) == "context_injection"


def test_context_injection_payload_round_trip() -> None:
    payload = ContextInjectionPayload(
        agent_id="a_1",
        items=[
            {
                "item_id": "v_1",
                "kind": "preference",
                "name": "form-of-address",
                "content": "Call the user Momo.",
                "reason": "global",
            }
        ],
    )
    dumped = payload.model_dump()
    assert dumped["agent_id"] == "a_1"
    assert dumped["items"][0]["reason"] == "global"


def test_context_injection_payload_rejects_bad_reason() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionPayload(
            agent_id="a_1",
            items=[
                {
                    "item_id": "v_1",
                    "kind": "skill",
                    "name": "n",
                    "content": "c",
                    "reason": "vibes",
                }
            ],
        )


def test_context_injection_payload_requires_agent_id() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionPayload(items=[])  # type: ignore[call-arg]


def test_assignments_new_fields_default_empty() -> None:
    assignments = AgentAssignments()
    assert assignments.tags == []
    assert assignments.preference_names == []
    assert assignments.effective_tags == []


def test_effective_tags_unions_alias_case_insensitively() -> None:
    assignments = AgentAssignments(
        tags=["Code", "python"], preference_tags=["code", "terse"]
    )
    # case-folded, order-stable dedup: "code" appears once, first position wins
    assert assignments.effective_tags == ["code", "python", "terse"]


def test_effective_tags_is_not_serialized() -> None:
    assignments = AgentAssignments(tags=["code"])
    assert "effective_tags" not in assignments.model_dump()
    assert "effective_tags" not in assignments.model_dump_json()
