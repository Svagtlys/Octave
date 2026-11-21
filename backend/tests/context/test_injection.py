"""Context injection engine (issue #34): selection + session-start durability.

Seeding mirrors tests/context/test_archiver.py: real SQLite via the shared
``env`` fixture (SqliteVecAdapter, session factory); vault writes go through
VaultStore (no embeddings — selection never touches the vector layer).
"""

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.context.budget import DEFAULT_CONTEXT_WINDOW
from octave.context.errors import (
    AgentNotFound,
    ContextBudgetExceededError,
    ParticipantNotFound,
)
from octave.context.injection import ContextBundle, ContextInjector
from octave.db.models import (
    Agent,
    Event,
    Participant,
    Session,
    SessionParticipant,
    User,
    VaultItem,
)
from octave.db.types import EventKind, VaultKind
from octave.db.vault_store import VaultStore
from octave.inference.errors import AdapterConnectionError
from octave.inference.types import ModelInfo


async def test_error_types_are_context_local(env) -> None:
    assert issubclass(AgentNotFound, Exception)
    assert issubclass(ParticipantNotFound, Exception)
    assert AgentNotFound("a_x").agent_id == "a_x"
    assert "a_x" in str(ParticipantNotFound("a_x"))
    assert AsyncSession  # keeps the import meaningful for mypy


def test_context_budget_exceeded_error_shape() -> None:
    from octave.context.errors import ContextBudgetExceededError

    err = ContextBudgetExceededError(
        session_id="s_1",
        agent_id="a_1",
        budget_tokens=1024,
        estimated_tokens=2000,
        drop_hint=[
            ("explicit-pref", "explicit"),
            ("global-pref", "global"),
            ("tagged-skill", "agent_tag"),
        ],
    )
    assert err.budget_tokens == 1024
    assert err.estimated_tokens == 2000
    message = str(err)
    assert "2000" in message and "1024" in message
    # lowest precedence first: agent_tag before global before explicit
    assert message.index("tagged-skill") < message.index("global-pref")
    assert message.index("global-pref") < message.index("explicit-pref")


async def _seed(
    env,
    *,
    sessions: dict[str, str],
    agents: dict[str, dict],
    participants: bool = True,
    users: tuple[str, ...] = ("u_1", "u_2"),
) -> None:
    """sessions: session_id -> owner user_id. agents: agent_id -> assignments.

    Membership rows accompany participants: ``events.target_participant_id``
    carries a composite FK onto ``session_participants`` (an event may only
    address a member of its own session), so injection writes need the agent
    joined to every session it will be injected into.
    """
    _adapter, factory = env
    async with factory() as s:
        for uid in users:
            s.add(User(id=uid, username=uid, display_name=uid))
        for agent_id, assignments in agents.items():
            s.add(Agent(id=agent_id, name=agent_id, assignments=dict(assignments)))
            if participants:
                s.add(
                    Participant(id=f"p_{agent_id}", agent_id=agent_id, label=agent_id)
                )
        for session_id, owner in sessions.items():
            s.add(Session(id=session_id, created_by_user_id=owner, status="active"))
        if participants:
            for uid in users:
                s.add(Participant(id=f"p_{uid}", user_id=uid, label=uid))
            for session_id, owner in sessions.items():
                s.add(
                    SessionParticipant(
                        session_id=session_id, participant_id=f"p_{owner}"
                    )
                )
                for agent_id in agents:
                    s.add(
                        SessionParticipant(
                            session_id=session_id, participant_id=f"p_{agent_id}"
                        )
                    )
        await s.commit()


async def _item(
    env,
    item_id: str,
    *,
    user_id: str,
    kind: VaultKind,
    name: str,
    content: str | None = None,
    tags: list[str] | None = None,
) -> None:
    _adapter, factory = env
    async with factory() as s:
        meta: dict = {} if tags is None else {"tags": tags}
        await VaultStore(_adapter, s).upsert(
            item_id=item_id,
            user_id=user_id,
            kind=kind,
            name=name,
            content=content or f"content of {name}",
            meta=meta,
        )
        await s.commit()


async def _select(env, session_id: str, agent_id: str) -> ContextBundle:
    _adapter, factory = env
    async with factory() as s:
        return await ContextInjector(s).select(session_id=session_id, agent_id=agent_id)


async def test_global_tag_injects_for_every_agent(env) -> None:
    await _seed(
        env, sessions={"s_1": "u_1"}, agents={"a_1": {}, "a_2": {"tags": ["code"]}}
    )
    await _item(
        env,
        "v_pref",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="addr",
        tags=["global"],
    )
    for agent_id in ("a_1", "a_2"):
        bundle = await _select(env, "s_1", agent_id)
        assert [(i.item_id, i.reason) for i in bundle.items] == [("v_pref", "global")]


async def test_agent_tag_match_selects_only_matching_agent(env) -> None:
    await _seed(
        env,
        sessions={"s_1": "u_1"},
        agents={"a_code": {"tags": ["code"]}, "a_cal": {}},
    )
    await _item(
        env,
        "v_depth",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="depth",
        tags=["code"],
    )
    await _item(
        env, "v_skill", user_id="u_1", kind=VaultKind.SKILL, name="py", tags=["code"]
    )
    matched = await _select(env, "s_1", "a_code")
    assert {i.item_id for i in matched.items} == {"v_depth", "v_skill"}
    assert all(i.reason == "agent_tag" for i in matched.items)
    unmatched = await _select(env, "s_1", "a_cal")
    assert unmatched.items == []


async def test_untagged_item_injects_only_when_named(env) -> None:
    await _seed(
        env, sessions={"s_1": "u_1"}, agents={"a_1": {}, "a_2": {"skills": ["solo"]}}
    )
    await _item(env, "v_solo", user_id="u_1", kind=VaultKind.SKILL, name="solo")
    assert (await _select(env, "s_1", "a_1")).items == []
    named = await _select(env, "s_1", "a_2")
    assert [(i.item_id, i.reason) for i in named.items] == [("v_solo", "explicit")]


async def test_explicit_is_additive_union(env) -> None:
    await _seed(
        env,
        sessions={"s_1": "u_1"},
        agents={"a_1": {"skills": ["named"], "preference_names": ["named-pref"]}},
    )
    await _item(env, "v_named", user_id="u_1", kind=VaultKind.SKILL, name="named")
    await _item(
        env, "v_global", user_id="u_1", kind=VaultKind.SKILL, name="g", tags=["global"]
    )
    await _item(
        env, "v_np", user_id="u_1", kind=VaultKind.PREFERENCE, name="named-pref"
    )
    await _item(
        env,
        "v_gp",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="g-pref",
        tags=["global"],
    )
    bundle = await _select(env, "s_1", "a_1")
    assert {i.item_id for i in bundle.items} == {"v_named", "v_global", "v_np", "v_gp"}


async def test_prompt_is_assignment_only(env) -> None:
    await _seed(
        env, sessions={"s_1": "u_1"}, agents={"a_1": {"prompt": "persona"}, "a_2": {}}
    )
    await _item(
        env,
        "v_persona",
        user_id="u_1",
        kind=VaultKind.PROMPT,
        name="persona",
        tags=["global"],
    )
    named = await _select(env, "s_1", "a_1")
    assert [(i.item_id, i.kind) for i in named.items] == [
        ("v_persona", VaultKind.PROMPT)
    ]
    other = await _select(env, "s_1", "a_2")
    assert other.items == []  # global tag does NOT pull prompts


async def test_owner_scoping_never_leaks_across_users(env) -> None:
    await _seed(
        env, sessions={"s_gorim": "u_1", "s_momo": "u_2"}, agents={"a_1": {}}
    )
    await _item(
        env, "v_g", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"]
    )
    await _item(
        env, "v_m", user_id="u_2", kind=VaultKind.PREFERENCE, name="p", tags=["global"]
    )
    gorim = await _select(env, "s_gorim", "a_1")
    momo = await _select(env, "s_momo", "a_1")
    assert [i.item_id for i in gorim.items] == ["v_g"]
    assert [i.item_id for i in momo.items] == ["v_m"]


async def test_tag_case_insensitivity(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"tags": ["Code"]}})
    await _item(env, "v", user_id="u_1", kind=VaultKind.SKILL, name="s", tags=["code"])
    bundle = await _select(env, "s_1", "a_1")
    assert [i.item_id for i in bundle.items] == ["v"]


async def test_reason_precedence_dedup(env) -> None:
    await _seed(
        env,
        sessions={"s_1": "u_1"},
        agents={"a_1": {"skills": ["dual"], "tags": ["code"]}},
    )
    await _item(
        env,
        "v_dual",
        user_id="u_1",
        kind=VaultKind.SKILL,
        name="dual",
        tags=["global", "code"],
    )
    bundle = await _select(env, "s_1", "a_1")
    assert [(i.item_id, i.reason) for i in bundle.items] == [("v_dual", "explicit")]


async def test_section_order_and_determinism(env) -> None:
    await _seed(
        env,
        sessions={"s_1": "u_1"},
        agents={"a_1": {"prompt": "persona", "preference_names": ["p"]}},
    )
    await _item(
        env, "v_skill", user_id="u_1", kind=VaultKind.SKILL, name="s", tags=["global"]
    )
    await _item(env, "v_pref", user_id="u_1", kind=VaultKind.PREFERENCE, name="p")
    await _item(env, "v_prompt", user_id="u_1", kind=VaultKind.PROMPT, name="persona")
    expected = ["v_prompt", "v_pref", "v_skill"]
    for _ in range(3):  # deterministic across repeated runs
        assert [i.item_id for i in (await _select(env, "s_1", "a_1")).items] == expected


async def test_dangling_reference_skips_with_warning(env, caplog) -> None:
    await _seed(
        env, sessions={"s_1": "u_1"}, agents={"a_1": {"skills": ["ghost", "real"]}}
    )
    await _item(env, "v_real", user_id="u_1", kind=VaultKind.SKILL, name="real")
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert [i.item_id for i in bundle.items] == ["v_real"]
    assert "ghost" in caplog.text


async def test_ambiguous_name_includes_all_with_warning(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"skills": ["dup"]}})
    await _item(env, "v_a", user_id="u_1", kind=VaultKind.SKILL, name="dup")
    await _item(env, "v_b", user_id="u_1", kind=VaultKind.SKILL, name="dup")
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert {i.item_id for i in bundle.items} == {"v_a", "v_b"}
    assert "dup" in caplog.text


async def test_malformed_meta_tags_treated_as_untagged(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"tags": ["code"]}})
    await _item(
        env, "v_bad", user_id="u_1", kind=VaultKind.SKILL, name="s", tags=["global"]
    )
    # corrupt the tags field after the fact (write-path bypass simulation)
    _adapter, factory = env
    async with factory() as s:
        item = await s.get(VaultItem, "v_bad")
        assert item is not None
        item.meta = {"tags": "not-a-list"}
        await s.commit()
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert bundle.items == []


async def test_corrupt_assignments_json_tolerated(env, caplog) -> None:
    _adapter, factory = env
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    async with factory() as s:
        agent = await s.get(Agent, "a_1")
        assert agent is not None
        agent.assignments = {"skills": "not-a-list"}
        await s.commit()
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert bundle.items == []
    assert "assignments" in caplog.text


async def test_select_raises_for_missing_session_and_agent(env) -> None:
    import pytest

    from octave.context.errors import SessionNotFound

    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})

    with pytest.raises(SessionNotFound):
        await _select(env, "s_missing", "a_1")
    with pytest.raises(AgentNotFound):
        await _select(env, "s_1", "a_missing")


async def _ensure(env, session_id: str, agent_id: str):
    _adapter, factory = env
    async with factory() as s:
        bundle = await ContextInjector(s).ensure_injected(
            session_id=session_id, agent_id=agent_id
        )
        await s.commit()
        return bundle


async def _injection_events(env) -> list[Event]:
    _adapter, factory = env
    async with factory() as s:
        rows = await s.execute(
            select(Event)
            .where(Event.kind == str(EventKind.CONTEXT_INJECTION))
            .order_by(Event.seq)
        )
        return list(rows.scalars().all())


async def test_ensure_injected_writes_one_targeted_snapshot(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(
        env, "v_pref", user_id="u_1", kind=VaultKind.PREFERENCE, name="addr",
        tags=["global"],
    )
    bundle = await _ensure(env, "s_1", "a_1")
    assert bundle is not None
    events = await _injection_events(env)
    assert len(events) == 1
    event = events[0]
    assert event.author_participant_id is None
    assert event.target_participant_id == "p_a_1"
    assert event.payload == {
        "agent_id": "a_1",
        "items": [
            {
                "item_id": "v_pref",
                "kind": "preference",
                "name": "addr",
                "content": "content of addr",
                "reason": "global",
            }
        ],
        # issue #36: no adapter seam → default 8192 window, 0.25 fraction;
        # estimate = ceil(len("content of addr") / 4)
        "budget_tokens": 2048,
        "estimated_tokens": 4,
    }


async def test_ensure_injected_is_idempotent(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(
        env, "v", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"]
    )
    assert await _ensure(env, "s_1", "a_1") is not None
    assert await _ensure(env, "s_1", "a_1") is None
    assert len(await _injection_events(env)) == 1


async def test_ensure_injected_one_event_per_agent(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}, "a_2": {}})
    await _item(
        env, "v", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"]
    )
    await _ensure(env, "s_1", "a_1")
    await _ensure(env, "s_1", "a_2")
    events = await _injection_events(env)
    assert sorted(e.target_participant_id for e in events) == ["p_a_1", "p_a_2"]


async def test_empty_selection_still_anchors_an_event(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    bundle = await _ensure(env, "s_1", "a_1")
    assert bundle is not None and bundle.items == []
    events = await _injection_events(env)
    assert len(events) == 1
    assert events[0].payload["items"] == []
    assert await _ensure(env, "s_1", "a_1") is None  # anchored even when empty


async def test_ensure_injected_requires_participant(env) -> None:
    import pytest

    await _seed(
        env, sessions={"s_1": "u_1"}, agents={"a_1": {}}, participants=False
    )
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ParticipantNotFound):
            await ContextInjector(s).ensure_injected(session_id="s_1", agent_id="a_1")


async def test_ensure_injected_requires_session_membership(env) -> None:
    """Participant row exists but the agent was never spawned into s_1:
    domain error, not the composite-FK IntegrityError at append time."""
    import pytest

    await _seed(
        env, sessions={"s_1": "u_1"}, agents={"a_1": {}}, participants=False
    )
    _adapter, factory = env
    async with factory() as s:
        s.add(Participant(id="p_a_1", agent_id="a_1", label="a_1"))
        await s.commit()
    async with factory() as s:
        with pytest.raises(ParticipantNotFound):
            await ContextInjector(s).ensure_injected(session_id="s_1", agent_id="a_1")
    assert await _injection_events(env) == []


async def test_rollback_leaves_no_event(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(
        env, "v", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"]
    )
    _adapter, factory = env
    async with factory() as s:
        await ContextInjector(s).ensure_injected(session_id="s_1", agent_id="a_1")
        await s.rollback()
    assert await _injection_events(env) == []


async def test_injection_payload_validated_on_append(env) -> None:
    import pytest
    from pydantic import ValidationError

    from octave.db.event_store import EventStore

    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ValidationError):
            await EventStore(s).append(
                "s_1", EventKind.CONTEXT_INJECTION, payload={"items": []}
            )  # agent_id missing


async def test_router_transcript_skips_injection_events(env) -> None:
    """_transcript_messages must ignore context_injection (chat history and
    the decider tail stay clean — design spec §Interaction)."""
    from octave.agent.router import (
        _transcript_messages,  # tests may import the agent plane
    )
    from octave.db.event_store import EventStore

    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    _adapter, factory = env
    async with factory() as s:
        store = EventStore(s)
        await store.append(
            "s_1",
            EventKind.USER_MESSAGE,
            author_participant_id="p_a_1",
            payload={"content": "hi"},
        )
        await store.append(
            "s_1",
            EventKind.CONTEXT_INJECTION,
            target_participant_id="p_a_1",
            payload={"agent_id": "a_1", "items": []},
        )
        events = await store.read("s_1")
        messages = _transcript_messages(events)
        assert [m.content for m in messages] == ["hi"]


def test_archiver_anchors_injection_events() -> None:
    """Unauthored context_injection is an anchor: excluded from brackets,
    its prose never chunked (already verbatim in the vault)."""
    from octave.context.brackets import reconstruct_turns
    from octave.db.models import Event

    def _event(seq: int, kind: EventKind, author: str | None) -> Event:
        return Event(
            id=f"e{seq}",
            session_id="s_1",
            seq=seq,
            kind=str(kind),
            author_participant_id=author,
            payload={},
        )

    events = [
        _event(1, EventKind.CONTEXT_INJECTION, None),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    brackets = reconstruct_turns(events, {"p_a1": "a_1"})
    assert [(b.seq_start, b.seq_end) for b in brackets] == [(2, 3)]


class _StubAdapter:
    """Duck-typed inference adapter: serves canned ModelInfo, counts calls."""

    def __init__(self, models: list[ModelInfo]) -> None:
        self._models = models
        self.calls = 0

    async def list_models(self) -> list[ModelInfo]:
        self.calls += 1
        return self._models


class _RaisingAdapter:
    async def list_models(self) -> list[ModelInfo]:
        raise AdapterConnectionError("engine down")


async def _set_model_binding(env, agent_id: str, binding: dict | None) -> None:
    _adapter, factory = env
    async with factory() as s:
        agent = await s.get(Agent, agent_id)
        assert agent is not None
        agent.model_binding = binding
        await s.commit()


async def _ensure_with(env, session_id: str, agent_id: str, **ctor_kwargs):
    _adapter, factory = env
    async with factory() as s:
        return await ContextInjector(s, **ctor_kwargs).ensure_injected(
            session_id=session_id, agent_id=agent_id
        ), ctor_kwargs.get("adapter_for")


async def test_over_budget_raises_without_writing(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    await _item(
        env,
        "v_big",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="big",
        tags=["global"],
        content="x" * 8000,
    )
    stub = _StubAdapter([ModelInfo(id="m_1", context_window=4096)])
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ContextBudgetExceededError) as excinfo:
            await ContextInjector(s, adapter_for=lambda name: stub).ensure_injected(
                session_id="s_1", agent_id="a_1"
            )
    assert excinfo.value.budget_tokens == 1024  # 4096 * 0.25
    assert excinfo.value.estimated_tokens == 2000  # 8000 / 4
    assert await _injection_events(env) == []  # nothing written


async def test_under_budget_event_records_budget_fields(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    await _item(
        env,
        "v_small",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="small",
        tags=["global"],
        content="x" * 400,
    )
    stub = _StubAdapter([ModelInfo(id="m_1", context_window=4096)])
    _adapter, factory = env
    async with factory() as s:
        bundle = await ContextInjector(
            s, adapter_for=lambda name: stub
        ).ensure_injected(session_id="s_1", agent_id="a_1")
        await s.commit()
    assert bundle is not None
    events = await _injection_events(env)
    assert events[0].payload["budget_tokens"] == 1024
    assert events[0].payload["estimated_tokens"] == 100


async def test_per_agent_budget_wins_when_smaller(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"token_budget": 50}})
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    await _item(
        env,
        "v_mid",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="mid",
        tags=["global"],
        content="x" * 600,
    )
    stub = _StubAdapter([ModelInfo(id="m_1", context_window=4096)])
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ContextBudgetExceededError) as excinfo:
            await ContextInjector(s, adapter_for=lambda name: stub).ensure_injected(
                session_id="s_1", agent_id="a_1"
            )
    assert excinfo.value.budget_tokens == 50  # per-agent beats ceiling 1024
    assert excinfo.value.estimated_tokens == 150


async def test_ceiling_wins_when_per_agent_larger(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"token_budget": 100_000}})
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    await _item(
        env,
        "v_big",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="big",
        tags=["global"],
        content="x" * 8000,
    )
    stub = _StubAdapter([ModelInfo(id="m_1", context_window=4096)])
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ContextBudgetExceededError) as excinfo:
            await ContextInjector(s, adapter_for=lambda name: stub).ensure_injected(
                session_id="s_1", agent_id="a_1"
            )
    assert excinfo.value.budget_tokens == 1024  # ceiling beats per-agent


async def test_no_adapter_seam_uses_default_window(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(
        env,
        "v_big",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="big",
        tags=["global"],
        content="x" * 12_000,
    )
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ContextBudgetExceededError) as excinfo:
            await ContextInjector(s).ensure_injected(session_id="s_1", agent_id="a_1")
    assert excinfo.value.budget_tokens == int(DEFAULT_CONTEXT_WINDOW * 0.25)


async def test_unreported_window_falls_back_to_default(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    await _item(
        env,
        "v_big",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="big",
        tags=["global"],
        content="x" * 12_000,
    )
    stub = _StubAdapter([ModelInfo(id="m_1")])  # context_window=None
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ContextBudgetExceededError) as excinfo:
            await ContextInjector(s, adapter_for=lambda name: stub).ensure_injected(
                session_id="s_1", agent_id="a_1"
            )
    assert excinfo.value.budget_tokens == int(DEFAULT_CONTEXT_WINDOW * 0.25)
    assert "no context window" in caplog.text


async def test_adapter_error_falls_back_to_default(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    await _item(
        env,
        "v_small",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="small",
        tags=["global"],
        content="tiny",
    )
    _adapter, factory = env
    async with factory() as s:
        bundle = await ContextInjector(
            s, adapter_for=lambda name: _RaisingAdapter()
        ).ensure_injected(session_id="s_1", agent_id="a_1")
    assert bundle is not None  # guard stays on at the default ceiling
    assert "lookup failed" in caplog.text


async def test_null_model_binding_uses_default(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(
        env,
        "v_small",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="small",
        tags=["global"],
        content="tiny",
    )
    bundle = await _ensure_with(
        env, "s_1", "a_1", adapter_for=lambda name: _StubAdapter([])
    )
    assert bundle[0] is not None
    assert "no model_binding" in caplog.text


async def test_non_positive_token_budget_treated_as_unset(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"token_budget": -5}})
    await _item(
        env,
        "v_small",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="small",
        tags=["global"],
        content="tiny",
    )
    stub = _StubAdapter([ModelInfo(id="m_1", context_window=4096)])
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    _adapter, factory = env
    async with factory() as s:
        bundle = await ContextInjector(
            s, adapter_for=lambda name: stub
        ).ensure_injected(session_id="s_1", agent_id="a_1")
        await s.commit()
    assert bundle is not None
    events = await _injection_events(env)
    assert events[0].payload["budget_tokens"] == 1024  # ceiling, not -5
    assert "not positive" in caplog.text


async def test_idempotence_skips_budget_work(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    stub = _StubAdapter([ModelInfo(id="m_1", context_window=4096)])
    _adapter, factory = env
    async with factory() as s:
        injector = ContextInjector(s, adapter_for=lambda name: stub)
        assert (
            await injector.ensure_injected(session_id="s_1", agent_id="a_1") is not None
        )
        await s.commit()
    # grow the bundle past the ceiling after injection: no re-check
    await _item(
        env,
        "v_big",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="big",
        tags=["global"],
        content="x" * 8000,
    )
    _adapter, factory = env
    async with factory() as s:
        assert (
            await ContextInjector(s, adapter_for=lambda name: stub).ensure_injected(
                session_id="s_1", agent_id="a_1"
            )
            is None
        )
    assert stub.calls == 1  # second call never touched list_models


def test_constructor_validates_wiring() -> None:
    with pytest.raises(ValueError):
        ContextInjector(None, context_window_fraction=0.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ContextInjector(None, context_window_fraction=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ContextInjector(None, default_context_window=0)  # type: ignore[arg-type]


async def test_drop_hint_ordered_lowest_precedence_first(env) -> None:
    # single seed (per plan note): the agent needs tags=["code"] for the
    # tagged skill to select via agent_tag, plus the explicit by-name pick.
    await _seed(
        env,
        sessions={"s_1": "u_1"},
        agents={
            "a_1": {
                "token_budget": 10,
                "tags": ["code"],
                "preference_names": ["chosen"],
            }
        },
    )
    await _set_model_binding(
        env, "a_1", {"kind": "explicit", "adapter": "openai", "model": "m_1"}
    )
    await _item(
        env, "v_tag", user_id="u_1", kind=VaultKind.SKILL, name="tagged", tags=["code"]
    )
    await _item(
        env,
        "v_glob",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="globby",
        tags=["global"],
    )
    await _item(
        env,
        "v_expl",
        user_id="u_1",
        kind=VaultKind.PREFERENCE,
        name="chosen",
        content="named",
    )
    stub = _StubAdapter([ModelInfo(id="m_1", context_window=4096)])
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ContextBudgetExceededError) as excinfo:
            await ContextInjector(s, adapter_for=lambda name: stub).ensure_injected(
                session_id="s_1", agent_id="a_1"
            )
    reasons = [r for _, r in excinfo.value.drop_hint]
    assert reasons == ["agent_tag", "global", "explicit"]
