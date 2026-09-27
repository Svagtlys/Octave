"""Agent registry: read-only queries over running instances (issue #26).

Rows in ``agent_instances`` are the durable record of what is running
(spawn inserts, destroy deletes — design spec 2026-09-27); this module is
the source-of-truth *view*, not a parallel bookkeeping structure. No states
added, no transitions enforced: ``AgentInstanceManager`` remains the only
write path. Follows the VaultStore convention — constructed with the
caller's AsyncSession, never commits, never mutates.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.errors import InstanceNotFoundError
from octave.db.models import Agent, AgentInstance
from octave.db.types import AgentAssignments, AgentStatus, InstanceStatus

__all__ = ["AgentRegistry", "RunningAgent"]

logger = logging.getLogger(__name__)

_ASSIGNMENTS_ADAPTER: TypeAdapter[AgentAssignments] = TypeAdapter(AgentAssignments)


@dataclass(frozen=True)
class RunningAgent:
    """One row of the registry view: instance fields + joined definition
    fields. ``model_binding`` is deliberately absent (design spec: IDs,
    lifecycle state, assigned context — add a field if the dashboard needs
    the binding)."""

    instance_id: str
    agent_id: str
    agent_name: str
    definition_status: AgentStatus
    instance_status: InstanceStatus
    session_id: str
    assignments: AgentAssignments
    created_at: datetime
    updated_at: datetime


def _to_running_agent(instance: AgentInstance, agent: Agent) -> RunningAgent:
    """Status parses are loud (a bad enum value is DB corruption; silently
    coercing it would misreport lifecycle state). Assignments parsing is
    made lenient in Task 4 (reporting surface, not a validation gate)."""
    return RunningAgent(
        instance_id=instance.id,
        agent_id=instance.agent_id,
        agent_name=agent.name,
        definition_status=AgentStatus(agent.status),
        instance_status=InstanceStatus(instance.status),
        session_id=instance.session_id,
        assignments=_ASSIGNMENTS_ADAPTER.validate_python(agent.assignments),
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


class AgentRegistry:
    """Read-only queries over ``agent_instances ⋈ agents``. The RESTRICT FK
    guarantees every instance has its definition row, so the join is safe;
    ``sessions`` is not joined — ``session_id`` lives on the instance row.
    Never commits; callers own transaction boundaries (``octave.db.deps``).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_instances(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        status: InstanceStatus | None = None,
    ) -> list[RunningAgent]:
        """Filters compose with AND; ``status`` is the *instance* status
        (idle | active), not the definition's. Ordering ``created_at ASC,
        id ASC`` — deterministic even on timestamp ties."""
        stmt = (
            select(AgentInstance, Agent)
            .join(Agent, AgentInstance.agent_id == Agent.id)
            .order_by(AgentInstance.created_at, AgentInstance.id)
        )
        if agent_id is not None:
            stmt = stmt.where(AgentInstance.agent_id == agent_id)
        if session_id is not None:
            stmt = stmt.where(AgentInstance.session_id == session_id)
        if status is not None:
            stmt = stmt.where(AgentInstance.status == str(status))
        rows = await self._session.execute(stmt)
        return [_to_running_agent(inst, ag) for inst, ag in rows]

    async def get_instance(self, instance_id: str) -> RunningAgent:
        """Raise on miss — same contract as the manager's ``_get``."""
        row = (
            await self._session.execute(
                select(AgentInstance, Agent)
                .join(Agent, AgentInstance.agent_id == Agent.id)
                .where(AgentInstance.id == instance_id)
            )
        ).first()
        if row is None:
            raise InstanceNotFoundError(instance_id)
        return _to_running_agent(row[0], row[1])

    async def count_by_status(self) -> dict[InstanceStatus, int]:
        """Dashboard counter over all rows (no filters — this is not a
        filtered aggregate). Both enum keys present, zero-filled."""
        rows = await self._session.execute(
            select(AgentInstance.status, func.count()).group_by(
                AgentInstance.status
            )
        )
        counts = {status: 0 for status in InstanceStatus}
        for raw, total in rows:
            counts[InstanceStatus(raw)] += int(total)
        return counts
