"""ORM models — the adapter-neutral schema.

All tables use TEXT primary keys (app-generated UUID4 hex) and timezone-aware
UTC timestamps. The enum-ish columns (``kind``/``status``/``role``/``mode``-like)
are TEXT with app-level validation so the enums can grow without migrations.
"""

from octave.db.models.base import Base, utcnow
from octave.db.models.core import Agent, Participant, User
from octave.db.models.sessions import Event, Session, SessionParticipant

__all__ = [
    "Agent",
    "Base",
    "Event",
    "Participant",
    "Session",
    "SessionParticipant",
    "User",
    "utcnow",
]
