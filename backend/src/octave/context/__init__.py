"""Context Manager service layer (issue #35).

Archives completed agent runs into the vault for future linked runs to
query. Imports ``octave.db`` + ``octave.inference`` only — never
``octave.agent`` (design spec 2026-09-30, Decision 9).
"""

from octave.context.archiver import ArchiveReport, ContextArchiver
from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.context.errors import (
    AgentNotFound,
    ModelBindingNotResolved,
    ParticipantNotFound,
    SessionNotFound,
)
from octave.context.injection import ContextBundle, ContextInjector, InjectedItem

__all__ = [
    "AgentNotFound",
    "ArchiveReport",
    "ContextArchiver",
    "ContextBundle",
    "ContextInjector",
    "InjectedItem",
    "ModelBindingNotResolved",
    "ParticipantNotFound",
    "SessionNotFound",
    "TurnBracket",
    "reconstruct_turns",
]
