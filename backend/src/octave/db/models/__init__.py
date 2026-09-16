"""ORM models — the adapter-neutral schema.

All tables use TEXT primary keys (app-generated UUID4 hex) and timezone-aware
UTC timestamps. The enum-ish columns (``kind``/``status``/``role``/``mode``-like)
are TEXT with app-level validation so the enums can grow without migrations.
"""

from octave.db.models.base import Base, utcnow
from octave.db.models.core import Agent, Participant, User
from octave.db.models.mcp import McpServer
from octave.db.models.sessions import Event, Session, SessionParticipant
from octave.db.models.vault import VaultItem

__all__ = [
    "Agent",
    "Base",
    "Event",
    "McpServer",
    "Participant",
    "Session",
    "SessionParticipant",
    "User",
    "VaultItem",
    "utcnow",
]
