"""Agent instance lifecycle (design spec 2026-09-27).

The only write path for ``agent_instances``. Follows the VaultStore
convention: constructed with the caller's AsyncSession, never commits.
Instances carry no context — the session transcript and the vault own it.
"""

import uuid

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.errors import (
    AgentNotFoundError,
    AgentPausedError,
    InstanceExistsError,
    InstanceNotFoundError,
    ModelBindingError,
    SessionNotFoundError,
    TerminalSessionError,
)
from octave.db.models import (
    Agent,
    AgentInstance,
    Participant,
    Session,
    SessionParticipant,
)
from octave.db.types import AgentStatus, InstanceStatus, ModelBinding

__all__ = ["AgentInstanceManager"]

_TERMINAL_SESSION_STATUSES = frozenset({"completed", "failed", "cancelled"})

_BINDING_ADAPTER: TypeAdapter[ModelBinding] = TypeAdapter(ModelBinding)


def _validate_binding(agent: Agent) -> None:
    """Spawn-time gate: shape only. Tag resolution happens at turn start
    via ``resolve_model`` (see design spec §4 and plan Deviations)."""
    if agent.model_binding is None:
        raise ModelBindingError(f"agent {agent.id} has no model_binding")
    try:
        _BINDING_ADAPTER.validate_python(agent.model_binding)
    except ValidationError as exc:
        raise ModelBindingError(
            f"agent {agent.id} has invalid model_binding: {exc}"
        ) from exc


class AgentInstanceManager:
    """spawn / begin_turn / end_turn / destroy / reconcile. Never commits;
    callers own transaction boundaries (see ``octave.db.deps``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def spawn(self, *, agent_id: str, session_id: str) -> AgentInstance:
        """Bind an active definition to a live session.

        Ensures the agent has a ``Participant`` row and active membership
        (re-invite resets ``left_at``). Raises the matching
        ``AgentError`` subclass on every gate.
        """
        agent = await self._session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        if agent.status != AgentStatus.ACTIVE:
            raise AgentPausedError(agent_id)
        _validate_binding(agent)
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFoundError(session_id)
        if session_row.status in _TERMINAL_SESSION_STATUSES:
            raise TerminalSessionError(session_id, session_row.status)
        existing = await self._session.execute(
            select(AgentInstance).where(
                AgentInstance.agent_id == agent_id,
                AgentInstance.session_id == session_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise InstanceExistsError(agent_id, session_id)
        participant = (
            await self._session.execute(
                select(Participant).where(Participant.agent_id == agent_id)
            )
        ).scalar_one_or_none()
        if participant is None:
            participant = Participant(
                id=uuid.uuid4().hex, agent_id=agent_id, label=agent.name
            )
            self._session.add(participant)
            await self._session.flush()
        membership = await self._session.get(
            SessionParticipant, (session_id, participant.id)
        )
        if membership is None:
            self._session.add(
                SessionParticipant(session_id=session_id, participant_id=participant.id)
            )
        elif membership.left_at is not None:
            membership.left_at = None  # re-invite: membership is live again
        instance = AgentInstance(
            id=uuid.uuid4().hex,
            agent_id=agent_id,
            session_id=session_id,
            status=str(InstanceStatus.IDLE),
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def _get(self, instance_id: str) -> AgentInstance:
        instance = await self._session.get(AgentInstance, instance_id)
        if instance is None:
            raise InstanceNotFoundError(instance_id)
        return instance
