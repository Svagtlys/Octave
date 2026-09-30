"""Context Manager service layer (issue #35).

Archives completed agent runs into the vault for future linked runs to
query. Imports ``octave.db`` + ``octave.inference`` only — never
``octave.agent`` (design spec 2026-09-30, Decision 9).
"""

from octave.context.archiver import ArchiveReport, ContextArchiver
from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.context.errors import ModelBindingNotResolved, SessionNotFound

__all__ = [
    "ArchiveReport",
    "ContextArchiver",
    "ModelBindingNotResolved",
    "SessionNotFound",
    "TurnBracket",
    "reconstruct_turns",
]
