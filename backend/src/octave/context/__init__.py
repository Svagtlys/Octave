"""Context Manager service layer (issue #35).

Archives completed agent runs into the vault for future linked runs to
query. Imports ``octave.db`` + ``octave.inference`` only — never
``octave.agent`` (design spec 2026-09-30, Decision 9).
"""

from octave.context.errors import ModelBindingNotResolved, SessionNotFound

__all__ = [
    "ModelBindingNotResolved",
    "SessionNotFound",
]
