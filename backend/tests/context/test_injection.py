"""Context injection engine (issue #34): selection + session-start durability.

Seeding mirrors tests/context/test_archiver.py: real SQLite via the shared
``env`` fixture (SqliteVecAdapter, session factory); vault writes go through
VaultStore (no embeddings — selection never touches the vector layer).
"""

from octave.context.errors import AgentNotFound, ParticipantNotFound


async def test_error_types_are_context_local(env) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert issubclass(AgentNotFound, Exception)
    assert issubclass(ParticipantNotFound, Exception)
    assert AgentNotFound("a_x").agent_id == "a_x"
    assert "a_x" in str(ParticipantNotFound("a_x"))
    assert AsyncSession  # keeps the import meaningful for mypy
