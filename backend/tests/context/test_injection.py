"""Context injection engine (issue #34): selection + session-start durability.

Seeding mirrors tests/context/test_archiver.py: real SQLite via the shared
``env`` fixture (SqliteVecAdapter, session factory); vault writes go through
VaultStore (no embeddings — selection never touches the vector layer).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.context.errors import AgentNotFound, ParticipantNotFound
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


async def test_error_types_are_context_local(env) -> None:
    assert issubclass(AgentNotFound, Exception)
    assert issubclass(ParticipantNotFound, Exception)
    assert AgentNotFound("a_x").agent_id == "a_x"
    assert "a_x" in str(ParticipantNotFound("a_x"))
    assert AsyncSession  # keeps the import meaningful for mypy


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
            s.add(User(id=uid, display_name=uid))
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
