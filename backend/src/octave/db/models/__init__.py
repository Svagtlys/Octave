"""ORM models — the adapter-neutral schema.

All tables use TEXT primary keys (app-generated UUID4 hex) and timezone-aware
UTC timestamps. The enum-ish columns (``kind``/``status``/``role``/``mode``-like)
are TEXT with app-level validation so the enums can grow without migrations.
"""

from octave.db.models.base import Base, utcnow
from octave.db.models.core import Agent, Participant, User

__all__ = [
    "Agent",
    "Base",
    "Participant",
    "User",
    "utcnow",
]
